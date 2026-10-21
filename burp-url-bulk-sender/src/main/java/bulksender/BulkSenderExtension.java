package bulksender;

import burp.api.montoya.BurpExtension;
import burp.api.montoya.MontoyaApi;
import burp.api.montoya.http.HttpService;
import burp.api.montoya.http.message.HttpRequestResponse;
import burp.api.montoya.http.message.requests.HttpRequest;
import burp.api.montoya.ui.editor.HttpRequestEditor;
import burp.api.montoya.ui.editor.HttpResponseEditor;

import javax.swing.*;
import javax.swing.border.TitledBorder;
import javax.swing.filechooser.FileNameExtensionFilter;
import javax.swing.table.TableRowSorter;
import java.awt.*;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.io.File;
import java.net.MalformedURLException;
import java.net.URI;
import java.net.URL;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Burp Suite extension — URL Bulk Sender.
 *
 * Loads a list of URLs, sends each with a selected HTTP method,
 * and displays results (status / length / time) in a sortable table.
 * Clicking any row shows the raw request and response in Burp's
 * native message editors.
 */
public class BulkSenderExtension implements BurpExtension {

    // ---------------------------------------------------------------- state
    private MontoyaApi api;
    private final ResultsTableModel tableModel = new ResultsTableModel();
    private volatile ExecutorService pool;
    private final AtomicBoolean running = new AtomicBoolean(false);

    // ---------------------------------------------------------------- widgets
    private JTextArea urlsArea;
    private JTextArea extraHeadersArea;
    private JTextArea requestBodyArea;
    private JComboBox<String> methodBox;
    private JSpinner threadsSpinner;
    private JSpinner timeoutSpinner;
    private JButton startBtn;
    private JButton stopBtn;
    private JLabel progressLabel;

    private HttpRequestEditor requestEditor;
    private HttpResponseEditor responseEditor;

    // ---------------------------------------------------------------- extension init
    @Override
    public void initialize(MontoyaApi api) {
        this.api = api;
        api.extension().setName("URL Bulk Sender");

        // Create Burp's native request/response editors (must be on EDT for Burp)
        SwingUtilities.invokeLater(() -> {
            requestEditor  = api.userInterface().createHttpRequestEditor();
            responseEditor = api.userInterface().createHttpResponseEditor();
            api.userInterface().registerSuiteTab("Bulk Sender", buildUI());
        });

        api.extension().registerUnloadingHandler(() -> {
            if (pool != null) pool.shutdownNow();
        });
    }

    // ================================================================ UI
    private JComponent buildUI() {

        // ---- top panel: URL input + controls ----
        urlsArea         = monoArea(10, 60);
        extraHeadersArea = monoArea(4, 40);
        requestBodyArea  = monoArea(4, 40);

        urlsArea.setToolTipText("One URL per line. http:// or https:// prefix optional (defaults to https).");
        extraHeadersArea.setToolTipText("Extra headers appended to every request, one per line: Header-Name: value");
        requestBodyArea.setToolTipText("Request body (used for POST/PUT/PATCH). Leave blank for bodyless methods.");

        methodBox     = new JComboBox<>(new String[]{"GET","POST","PUT","DELETE","PATCH","HEAD","OPTIONS","TRACE"});
        threadsSpinner = new JSpinner(new SpinnerNumberModel(5, 1, 100, 1));
        timeoutSpinner = new JSpinner(new SpinnerNumberModel(30, 1, 600, 1));
        startBtn      = new JButton("▶  Start");
        stopBtn       = new JButton("■  Stop");
        stopBtn.setEnabled(false);
        JButton loadFileBtn  = new JButton("Load URLs from file…");
        JButton clearResBtn  = new JButton("Clear results");
        progressLabel = new JLabel("Idle");

        startBtn.addActionListener(e    -> startSending());
        stopBtn.addActionListener(e     -> stopSending());
        loadFileBtn.addActionListener(e -> loadUrlsFromFile());
        clearResBtn.addActionListener(e -> {
            tableModel.clear();
            requestEditor.setRequest(HttpRequest.httpRequestFromUrl("https://placeholder.invalid/"));
            responseEditor.setResponse(null);
            progressLabel.setText("Idle");
        });

        // Control bar
        JPanel controls = new JPanel(new FlowLayout(FlowLayout.LEFT, 8, 4));
        controls.add(label("Method:")); controls.add(methodBox);
        controls.add(label("Threads:")); controls.add(threadsSpinner);
        controls.add(label("Timeout (s):")); controls.add(timeoutSpinner);
        controls.add(startBtn);
        controls.add(stopBtn);
        controls.add(loadFileBtn);
        controls.add(clearResBtn);
        controls.add(progressLabel);

        // Left column: URLs + optional headers + body
        JPanel inputPanel = new JPanel(new GridBagLayout());
        GridBagConstraints gbc = new GridBagConstraints();
        gbc.fill = GridBagConstraints.BOTH;
        gbc.insets = new Insets(2,4,2,4);
        gbc.gridx = 0; gbc.gridy = 0; gbc.weightx = 1; gbc.weighty = 3;
        inputPanel.add(titledScroll("URLs  (one per line)", urlsArea), gbc);
        gbc.gridy = 1; gbc.weighty = 1;
        inputPanel.add(titledScroll("Extra Headers  (Header: value)", extraHeadersArea), gbc);
        gbc.gridy = 2; gbc.weighty = 1;
        inputPanel.add(titledScroll("Request Body  (POST / PUT / PATCH)", requestBodyArea), gbc);

        // ---- results table ----
        JTable table = new JTable(tableModel);
        TableRowSorter<ResultsTableModel> sorter = new TableRowSorter<>(tableModel);
        table.setRowSorter(sorter);
        table.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
        table.setAutoResizeMode(JTable.AUTO_RESIZE_ALL_COLUMNS);
        table.getTableHeader().setReorderingAllowed(false);
        table.setFont(new Font(Font.MONOSPACED, Font.PLAIN, 12));
        table.setRowHeight(20);

        // Column widths
        table.getColumnModel().getColumn(0).setPreferredWidth(40);   // #
        table.getColumnModel().getColumn(1).setPreferredWidth(420);  // URL
        table.getColumnModel().getColumn(2).setPreferredWidth(65);   // Method
        table.getColumnModel().getColumn(3).setPreferredWidth(65);   // Status
        table.getColumnModel().getColumn(4).setPreferredWidth(110);  // Length
        table.getColumnModel().getColumn(5).setPreferredWidth(90);   // Time

        // Coloured status column
        table.getColumnModel().getColumn(3).setCellRenderer(new StatusCellRenderer());

        // Row click → populate request/response editors
        table.addMouseListener(new MouseAdapter() {
            @Override public void mouseClicked(MouseEvent e) {
                int viewRow = table.getSelectedRow();
                if (viewRow < 0) return;
                int modelRow = table.convertRowIndexToModel(viewRow);
                ResultEntry entry = tableModel.getEntry(modelRow);
                if (entry.getRequestResponse() != null) {
                    HttpRequestResponse rr = entry.getRequestResponse();
                    requestEditor.setRequest(rr.request());
                    if (rr.response() != null) {
                        responseEditor.setResponse(rr.response());
                    }
                }
            }
        });

        JScrollPane tableScroll = new JScrollPane(table);
        tableScroll.setBorder(BorderFactory.createTitledBorder("Results"));

        // ---- request / response viewer (bottom) ----
        JSplitPane reqRespSplit = new JSplitPane(
                JSplitPane.HORIZONTAL_SPLIT,
                titledComponent("Request",  requestEditor.uiComponent()),
                titledComponent("Response", responseEditor.uiComponent())
        );
        reqRespSplit.setResizeWeight(0.5);
        reqRespSplit.setDividerSize(4);

        // ---- layout: top = input | table, bottom = editors ----
        JSplitPane topSplit = new JSplitPane(
                JSplitPane.HORIZONTAL_SPLIT,
                inputPanel,
                tableScroll
        );
        topSplit.setResizeWeight(0.25);
        topSplit.setDividerSize(4);

        JSplitPane mainSplit = new JSplitPane(
                JSplitPane.VERTICAL_SPLIT,
                topSplit,
                reqRespSplit
        );
        mainSplit.setResizeWeight(0.55);
        mainSplit.setDividerSize(4);

        JPanel root = new JPanel(new BorderLayout());
        root.add(controls, BorderLayout.NORTH);
        root.add(mainSplit, BorderLayout.CENTER);
        return root;
    }

    // ================================================================ Sending logic

    private void startSending() {
        if (running.get()) return;

        List<String> urls = collectUrls();
        if (urls.isEmpty()) {
            JOptionPane.showMessageDialog(null, "No URLs to send. Add at least one URL.", "Bulk Sender", JOptionPane.WARNING_MESSAGE);
            return;
        }

        String method         = (String) methodBox.getSelectedItem();
        String extraHeaders   = extraHeadersArea.getText().trim();
        String body           = requestBodyArea.getText();
        int    threads        = (int) threadsSpinner.getValue();
        int    timeoutSeconds = (int) timeoutSpinner.getValue();

        tableModel.clear();
        running.set(true);
        startBtn.setEnabled(false);
        stopBtn.setEnabled(true);
        progressLabel.setText("Running…  0 / " + urls.size());

        pool = Executors.newFixedThreadPool(threads);
        AtomicInteger done = new AtomicInteger(0);
        int total = urls.size();

        for (int i = 0; i < total; i++) {
            final int idx    = i + 1;
            final String url = urls.get(i);
            pool.submit(() -> {
                if (!running.get()) return;
                ResultEntry entry = sendOne(idx, url, method, extraHeaders, body, timeoutSeconds);
                tableModel.addRow(entry);
                int n = done.incrementAndGet();
                SwingUtilities.invokeLater(() ->
                        progressLabel.setText("Running…  " + n + " / " + total));
            });
        }

        // Monitor for completion
        Thread monitor = new Thread(() -> {
            pool.shutdown();
            try {
                pool.awaitTermination(Long.MAX_VALUE, TimeUnit.DAYS);
            } catch (InterruptedException ignored) {}
            SwingUtilities.invokeLater(() -> {
                running.set(false);
                startBtn.setEnabled(true);
                stopBtn.setEnabled(false);
                progressLabel.setText("Done — " + tableModel.getRowCount() + " results");
            });
        });
        monitor.setDaemon(true);
        monitor.start();
    }

    private void stopSending() {
        running.set(false);
        if (pool != null) pool.shutdownNow();
        startBtn.setEnabled(true);
        stopBtn.setEnabled(false);
        progressLabel.setText("Stopped");
    }

    private ResultEntry sendOne(int idx, String rawUrl, String method,
                                String extraHeaders, String body, int timeoutSec) {
        // Normalise URL
        String urlStr = rawUrl.trim();
        if (urlStr.isEmpty()) return null;
        if (!urlStr.startsWith("http://") && !urlStr.startsWith("https://"))
            urlStr = "https://" + urlStr;

        try {
            URL parsed = URI.create(urlStr).toURL();
            boolean useHttps = "https".equalsIgnoreCase(parsed.getProtocol());
            int port = parsed.getPort();
            if (port == -1) port = useHttps ? 443 : 80;

            String path = parsed.getFile();
            if (path == null || path.isEmpty()) path = "/";

            HttpService svc = HttpService.httpService(parsed.getHost(), port, useHttps);

            // Build the request line + mandatory headers
            StringBuilder rawReq = new StringBuilder();
            rawReq.append(method).append(" ").append(path).append(" HTTP/1.1\r\n");
            rawReq.append("Host: ").append(parsed.getHost())
                  .append(port == (useHttps ? 443 : 80) ? "" : ":" + port).append("\r\n");
            rawReq.append("Connection: close\r\n");
            rawReq.append("User-Agent: BurpSuite-BulkSender/1.0\r\n");
            rawReq.append("Accept: */*\r\n");

            // Extra headers
            if (!extraHeaders.isEmpty()) {
                for (String hdr : extraHeaders.split("\\r?\\n")) {
                    hdr = hdr.trim();
                    if (!hdr.isEmpty()) rawReq.append(hdr).append("\r\n");
                }
            }

            // Body
            byte[] bodyBytes = body.getBytes(java.nio.charset.StandardCharsets.UTF_8);
            boolean hasBody  = bodyBytes.length > 0;
            if (hasBody) {
                rawReq.append("Content-Length: ").append(bodyBytes.length).append("\r\n");
                rawReq.append("Content-Type: application/x-www-form-urlencoded\r\n");
            }
            rawReq.append("\r\n");

            // Build Montoya request
            HttpRequest req;
            if (hasBody) {
                byte[] headerBytes = rawReq.toString().getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
                byte[] full = new byte[headerBytes.length + bodyBytes.length];
                System.arraycopy(headerBytes, 0, full, 0, headerBytes.length);
                System.arraycopy(bodyBytes,   0, full, headerBytes.length, bodyBytes.length);
                req = HttpRequest.httpRequest(svc, burp.api.montoya.core.ByteArray.byteArray(full));
            } else {
                req = HttpRequest.httpRequest(svc,
                        burp.api.montoya.core.ByteArray.byteArray(
                                rawReq.toString().getBytes(java.nio.charset.StandardCharsets.ISO_8859_1)));
            }

            long t0 = System.currentTimeMillis();
            HttpRequestResponse rr = api.http().sendRequest(req);
            long elapsed = System.currentTimeMillis() - t0;

            if (rr.response() == null) {
                return new ResultEntry(idx, urlStr, method, "No response");
            }

            int    status = rr.response().statusCode();
            long   length = rr.response().toByteArray().length();
            return new ResultEntry(idx, urlStr, method, status, elapsed, length, rr);

        } catch (MalformedURLException | IllegalArgumentException e) {
            return new ResultEntry(idx, urlStr, method, "Bad URL: " + e.getMessage());
        } catch (Exception e) {
            return new ResultEntry(idx, urlStr, method, e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
        }
    }

    // ================================================================ helpers

    private List<String> collectUrls() {
        List<String> list = new ArrayList<>();
        for (String line : urlsArea.getText().split("\\r?\\n")) {
            String t = line.trim();
            if (!t.isEmpty() && !t.startsWith("#")) list.add(t);
        }
        return list;
    }

    private void loadUrlsFromFile() {
        JFileChooser fc = new JFileChooser();
        fc.setDialogTitle("Load URLs from file");
        fc.setFileFilter(new FileNameExtensionFilter("Text files (*.txt)", "txt"));
        fc.setAcceptAllFileFilterUsed(true);
        if (fc.showOpenDialog(null) != JFileChooser.APPROVE_OPTION) return;
        File f = fc.getSelectedFile();
        try {
            String content = Files.readString(f.toPath());
            String existing = urlsArea.getText().trim();
            urlsArea.setText(existing.isEmpty() ? content : existing + "\n" + content);
        } catch (Exception e) {
            JOptionPane.showMessageDialog(null, "Could not read file:\n" + e.getMessage(),
                    "Error", JOptionPane.ERROR_MESSAGE);
        }
    }

    private static JTextArea monoArea(int rows, int cols) {
        JTextArea a = new JTextArea(rows, cols);
        a.setFont(new Font(Font.MONOSPACED, Font.PLAIN, 12));
        a.setLineWrap(false);
        return a;
    }

    private static JScrollPane titledScroll(String title, JTextArea area) {
        JScrollPane sp = new JScrollPane(area);
        sp.setBorder(BorderFactory.createTitledBorder(
                BorderFactory.createEtchedBorder(), title,
                TitledBorder.LEFT, TitledBorder.TOP));
        return sp;
    }

    private static JComponent titledComponent(String title, Component inner) {
        JPanel p = new JPanel(new BorderLayout());
        p.setBorder(BorderFactory.createTitledBorder(
                BorderFactory.createEtchedBorder(), title,
                TitledBorder.LEFT, TitledBorder.TOP));
        p.add(inner, BorderLayout.CENTER);
        return p;
    }

    private static JLabel label(String text) {
        return new JLabel(text);
    }
}
