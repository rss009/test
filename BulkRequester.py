# -*- coding: utf-8 -*-
"""
Bulk Requester - Burp Suite extension (Jython 2.7)

Run one HTTP request template against many URLs concurrently, review the
results in a sortable/filterable table, inspect any request/response, export
to CSV and send interesting rows to Repeater.

Load via Extender > Extensions > Add > Extension type: Python.
Use only against systems you are authorised to test.
"""

import csv
import io
import threading
import time

from burp import IBurpExtender, ITab, IMessageEditorController, IExtensionStateListener
from java.awt import BorderLayout, FlowLayout, GridLayout, Dimension, Toolkit
from java.awt.datatransfer import StringSelection
from java.awt.event import ActionListener, MouseAdapter
from java.lang import Runnable, Integer, String
from java.net import URL
from java.util.concurrent import Executors
from javax.swing import (BorderFactory, JButton, JCheckBox, JComboBox, JFileChooser,
                         JLabel, JMenuItem, JOptionPane, JPanel, JPopupMenu,
                         JProgressBar, JScrollPane, JSpinner, JSplitPane, JTabbedPane,
                         JTable, JTextArea, JTextField, RowFilter, SpinnerNumberModel,
                         SwingUtilities)
from javax.swing.event import DocumentListener, ListSelectionListener
from javax.swing.table import AbstractTableModel, TableRowSorter

CONFIRM_REPEATER_OVER = 20
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"]
STATUS_FILTERS = ["All", "2xx", "3xx", "4xx", "5xx", "Errors / no response"]


# --------------------------------------------------------------------------
# Small Swing glue helpers
# --------------------------------------------------------------------------
class Run(Runnable):
    def __init__(self, fn, *args):
        self.fn = fn
        self.args = args

    def run(self):
        self.fn(*self.args)


class Act(ActionListener):
    def __init__(self, fn):
        self.fn = fn

    def actionPerformed(self, event):
        self.fn()


class DocChange(DocumentListener):
    def __init__(self, fn):
        self.fn = fn

    def insertUpdate(self, e):
        self.fn()

    def removeUpdate(self, e):
        self.fn()

    def changedUpdate(self, e):
        self.fn()


class SelChange(ListSelectionListener):
    def __init__(self, fn):
        self.fn = fn

    def valueChanged(self, e):
        if not e.getValueIsAdjusting():
            self.fn()


class PopupHandler(MouseAdapter):
    def __init__(self, fn):
        self.fn = fn

    def mousePressed(self, e):
        if e.isPopupTrigger():
            self.fn(e)

    def mouseReleased(self, e):
        if e.isPopupTrigger():
            self.fn(e)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
class Result(object):
    def __init__(self, idx, method, url):
        self.idx = idx
        self.method = method
        self.url = url
        self.status = 0
        self.length = 0
        self.ms = 0
        self.error = ""
        self.request = None
        self.response = None
        self.service = None
        self.host = None
        self.port = 0
        self.https = False
        self._body_lc = None

    def body_lc(self, helpers):
        if self._body_lc is None:
            self._body_lc = helpers.bytesToString(self.response or []).lower()
        return self._body_lc


class ResultsModel(AbstractTableModel):
    COLS = ["#", "Method", "URL", "Status", "Length", "Time (ms)", "Error"]

    def __init__(self):
        self.rows = []

    def getRowCount(self):
        return len(self.rows)

    def getColumnCount(self):
        return len(self.COLS)

    def getColumnName(self, c):
        return self.COLS[c]

    def getColumnClass(self, c):
        if c in (0, 3, 4, 5):
            return Integer(0).getClass()
        return String("").getClass()

    def getValueAt(self, row, col):
        r = self.rows[row]
        return (r.idx, r.method, r.url, r.status, r.length, r.ms, r.error)[col]

    def add(self, result):
        self.rows.append(result)
        n = len(self.rows) - 1
        self.fireTableRowsInserted(n, n)

    def clear(self):
        self.rows = []
        self.fireTableDataChanged()

    def remove_rows(self, indexes):
        for i in sorted(indexes, reverse=True):
            del self.rows[i]
        self.fireTableDataChanged()


class ResultFilter(RowFilter):
    def __init__(self, ext):
        self.ext = ext

    def include(self, entry):
        return self.ext.matches(self.ext.model.rows[entry.getIdentifier()])


# --------------------------------------------------------------------------
# Extension
# --------------------------------------------------------------------------
class BurpExtender(IBurpExtender, ITab, IMessageEditorController, IExtensionStateListener):

    # ---- IBurpExtender ---------------------------------------------------
    def registerExtenderCallbacks(self, callbacks):
        self.callbacks = callbacks
        self.helpers = callbacks.getHelpers()
        callbacks.setExtensionName("Bulk Requester")
        callbacks.registerExtensionStateListener(self)

        self.model = ResultsModel()
        self.current = None
        self.executor = None
        self.lock = threading.Lock()
        self.run_id = 0
        self.total = 0
        self.done = 0
        self.running = False

        SwingUtilities.invokeLater(Run(self.build_ui))

    def extensionUnloaded(self):
        self.stop(silent=True)

    # ---- ITab ------------------------------------------------------------
    def getTabCaption(self):
        return "Bulk Requester"

    def getUiComponent(self):
        return self.root

    # ---- IMessageEditorController ---------------------------------------
    def getHttpService(self):
        return self.current.service if self.current else None

    def getRequest(self):
        return self.current.request if self.current else None

    def getResponse(self):
        return self.current.response if self.current else None

    # ---- UI --------------------------------------------------------------
    def build_ui(self):
        # Inputs
        self.urlsArea = JTextArea(8, 40)
        self.headersArea = JTextArea(8, 30)
        self.bodyArea = JTextArea(8, 30)
        self.headersArea.setToolTipText("One per line:  Name: value   (Host/Content-Length are handled for you)")
        self.urlsArea.setToolTipText("One URL per line. Missing scheme defaults to https://. Lines starting with # are ignored.")

        inputs = JPanel(GridLayout(1, 3, 6, 0))
        for title, area in (("URLs (one per line)", self.urlsArea),
                            ("Custom headers", self.headersArea),
                            ("Body (POST/PUT/PATCH/...)", self.bodyArea)):
            p = JPanel(BorderLayout())
            p.setBorder(BorderFactory.createTitledBorder(title))
            p.add(JScrollPane(area), BorderLayout.CENTER)
            inputs.add(p)

        # Controls
        self.methodBox = JComboBox(METHODS)
        self.methodBox.setEditable(True)
        self.threadSpin = JSpinner(SpinnerNumberModel(10, 1, 200, 1))
        self.startBtn = JButton("Start")
        self.startBtn.addActionListener(Act(self.start))
        self.stopBtn = JButton("Stop")
        self.stopBtn.addActionListener(Act(self.stop))
        self.stopBtn.setEnabled(False)
        loadBtn = JButton("Load URLs from file...")
        loadBtn.addActionListener(Act(self.load_urls))
        self.progress = JProgressBar(0, 100)
        self.progress.setStringPainted(True)
        self.progress.setPreferredSize(Dimension(180, 20))

        controls = JPanel(FlowLayout(FlowLayout.LEFT))
        controls.add(JLabel("Method:"))
        controls.add(self.methodBox)
        controls.add(JLabel("Threads:"))
        controls.add(self.threadSpin)
        controls.add(self.startBtn)
        controls.add(self.stopBtn)
        controls.add(loadBtn)
        controls.add(self.progress)

        top = JPanel(BorderLayout())
        top.add(inputs, BorderLayout.CENTER)
        top.add(controls, BorderLayout.SOUTH)

        # Filter bar
        self.filterText = JTextField(24)
        self.filterText.getDocument().addDocumentListener(DocChange(self.apply_filter))
        self.filterStatus = JComboBox(STATUS_FILTERS)
        self.filterStatus.addActionListener(Act(self.apply_filter))
        self.filterBody = JCheckBox("Search response body too")
        self.filterBody.addActionListener(Act(self.apply_filter))
        exportBtn = JButton("Export CSV...")
        exportBtn.addActionListener(Act(self.export_csv))
        repeaterBtn = JButton("Send selected to Repeater")
        repeaterBtn.addActionListener(Act(self.send_to_repeater))
        clearBtn = JButton("Clear")
        clearBtn.addActionListener(Act(self.clear))

        bar = JPanel(FlowLayout(FlowLayout.LEFT))
        bar.add(JLabel("Filter:"))
        bar.add(self.filterText)
        bar.add(self.filterStatus)
        bar.add(self.filterBody)
        bar.add(exportBtn)
        bar.add(repeaterBtn)
        bar.add(clearBtn)

        # Results table
        self.table = JTable(self.model)
        self.sorter = TableRowSorter(self.model)
        self.table.setRowSorter(self.sorter)
        self.sorter.setRowFilter(ResultFilter(self))
        self.table.getSelectionModel().addListSelectionListener(SelChange(self.on_select))
        self.table.addMouseListener(PopupHandler(self.show_popup))
        for i, w in enumerate((50, 70, 480, 70, 80, 80, 220)):
            self.table.getColumnModel().getColumn(i).setPreferredWidth(w)

        tablePane = JPanel(BorderLayout())
        tablePane.add(bar, BorderLayout.NORTH)
        tablePane.add(JScrollPane(self.table), BorderLayout.CENTER)

        # Viewers
        self.requestViewer = self.callbacks.createMessageEditor(self, False)
        self.responseViewer = self.callbacks.createMessageEditor(self, False)
        tabs = JTabbedPane()
        tabs.addTab("Request", self.requestViewer.getComponent())
        tabs.addTab("Response", self.responseViewer.getComponent())

        lower = JSplitPane(JSplitPane.VERTICAL_SPLIT, tablePane, tabs)
        lower.setResizeWeight(0.5)
        self.root = JSplitPane(JSplitPane.VERTICAL_SPLIT, top, lower)
        self.root.setResizeWeight(0.3)

        # Right-click menu
        self.popup = JPopupMenu()
        for label, fn in (("Send to Repeater", self.send_to_repeater),
                          ("Copy URL(s)", self.copy_urls),
                          ("Remove from list", self.remove_selected)):
            item = JMenuItem(label)
            item.addActionListener(Act(fn))
            self.popup.add(item)

        self.callbacks.customizeUiComponent(self.root)
        self.callbacks.addSuiteTab(self)

    # ---- Input helpers ---------------------------------------------------
    def parse_urls(self):
        out = []
        for line in self.urlsArea.getText().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "://" not in line:
                line = "https://" + line
            out.append(line)
        return out

    def parse_headers(self):
        out = []
        for line in self.headersArea.getText().splitlines():
            line = line.strip()
            if ":" in line:
                name, value = line.split(":", 1)
                if name.strip():
                    out.append((name.strip(), value.strip()))
        return out

    def load_urls(self):
        fc = JFileChooser()
        if fc.showOpenDialog(self.root) != JFileChooser.APPROVE_OPTION:
            return
        try:
            with io.open(fc.getSelectedFile().getAbsolutePath(), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception as e:
            JOptionPane.showMessageDialog(self.root, "Could not read file: %s" % e)
            return
        existing = self.urlsArea.getText()
        if existing and not existing.endswith("\n"):
            existing += "\n"
        self.urlsArea.setText(existing + text)

    # ---- Request building ------------------------------------------------
    def build_request(self, method, url_str, headers, body):
        """Returns (request_bytes, host, port, https). Raises on bad URL."""
        u = URL(url_str)
        proto = u.getProtocol().lower()
        if proto not in ("http", "https"):
            raise ValueError("unsupported scheme: %s" % proto)
        https = proto == "https"
        port = u.getPort()
        default_port = 443 if https else 80
        if port == -1:
            port = default_port
        host = u.getHost()
        if not host:
            raise ValueError("no host in URL")
        hostport = host if port == default_port else "%s:%d" % (host, port)
        path = u.getFile() or "/"
        if not path.startswith("/"):
            path = "/" + path

        names = set(n.lower() for n, _ in headers)
        body_bytes = body.encode("utf-8") if body and method not in ("GET", "HEAD") else b""

        lines = [u"%s %s HTTP/1.1" % (method, path)]
        if "host" not in names:
            lines.append(u"Host: " + hostport)
        for n, v in headers:
            if n.lower() == "content-length":
                continue  # computed below
            lines.append(u"%s: %s" % (n, v))
        if "user-agent" not in names:
            lines.append(u"User-Agent: BulkRequester")
        if "accept" not in names:
            lines.append(u"Accept: */*")
        if "connection" not in names:
            lines.append(u"Connection: close")
        if body_bytes:
            if "content-type" not in names:
                lines.append(u"Content-Type: application/x-www-form-urlencoded")
        if body_bytes or method in ("POST", "PUT", "PATCH"):
            lines.append(u"Content-Length: %d" % len(body_bytes))

        raw = (u"\r\n".join(lines) + u"\r\n\r\n").encode("utf-8") + body_bytes
        return self.helpers.stringToBytes(raw), host, port, https

    # ---- Run control -----------------------------------------------------
    def start(self):
        if self.running:
            return
        urls = self.parse_urls()
        if not urls:
            JOptionPane.showMessageDialog(self.root, "Add at least one URL first.")
            return
        method = (unicode(self.methodBox.getSelectedItem() or u"GET")).strip().upper() or u"GET"
        if any(c in method for c in " \r\n\t"):
            JOptionPane.showMessageDialog(self.root, "Invalid HTTP method.")
            return
        headers = self.parse_headers()
        body = self.bodyArea.getText()
        threads = int(self.threadSpin.getValue())

        self.run_id += 1
        run_id = self.run_id
        self.total = len(urls)
        self.done = 0
        self.running = True
        self.model.clear()
        self.current = None
        self.progress.setMaximum(self.total)
        self.progress.setValue(0)
        self.progress.setString("0 / %d" % self.total)
        self.startBtn.setEnabled(False)
        self.stopBtn.setEnabled(True)

        self.executor = Executors.newFixedThreadPool(threads)
        for i, url in enumerate(urls):
            self.executor.submit(Run(self.work, run_id, i + 1, method, url, headers, body))
        self.executor.shutdown()  # no new tasks; queued ones still run

    def stop(self, silent=False):
        self.run_id += 1  # orphan in-flight results
        if self.executor is not None:
            self.executor.shutdownNow()
            self.executor = None
        self.running = False
        if not silent:
            SwingUtilities.invokeLater(Run(self.set_idle))

    def set_idle(self):
        self.startBtn.setEnabled(True)
        self.stopBtn.setEnabled(False)

    def clear(self):
        self.stop()
        self.model.clear()
        self.current = None
        self.requestViewer.setMessage(b"", True)
        self.responseViewer.setMessage(b"", False)
        self.progress.setValue(0)
        self.progress.setString("")

    # ---- Worker (runs on pool threads) ----------------------------------
    def work(self, run_id, idx, method, url, headers, body):
        if run_id != self.run_id:
            return
        r = Result(idx, method, url)
        start = time.time()
        try:
            req, host, port, https = self.build_request(method, url, headers, body)
            r.request, r.host, r.port, r.https = req, host, port, https
            r.service = self.helpers.buildHttpService(host, port, https)
            start = time.time()
            rr = self.callbacks.makeHttpRequest(r.service, req)
            r.ms = int((time.time() - start) * 1000)
            resp = rr.getResponse()
            if resp is None:
                r.error = "No response"
            else:
                r.response = resp
                r.length = len(resp)
                r.status = self.helpers.analyzeResponse(resp).getStatusCode()
        except Exception as e:
            r.ms = int((time.time() - start) * 1000)
            r.error = str(e)[:200]
        if run_id == self.run_id:
            SwingUtilities.invokeLater(Run(self.publish, run_id, r))

    def publish(self, run_id, r):  # EDT
        if run_id != self.run_id:
            return
        self.model.add(r)
        self.done += 1
        self.progress.setValue(self.done)
        self.progress.setString("%d / %d" % (self.done, self.total))
        if self.done >= self.total:
            self.running = False
            self.set_idle()

    # ---- Table behaviour -------------------------------------------------
    def selected_results(self):
        out = []
        for v in self.table.getSelectedRows():
            out.append(self.model.rows[self.table.convertRowIndexToModel(v)])
        return out

    def on_select(self):
        sel = self.selected_results()
        self.current = sel[0] if sel else None
        r = self.current
        self.requestViewer.setMessage(r.request if r and r.request else b"", True)
        self.responseViewer.setMessage(r.response if r and r.response else b"", False)

    def show_popup(self, e):
        row = self.table.rowAtPoint(e.getPoint())
        if row >= 0 and not self.table.isRowSelected(row):
            self.table.setRowSelectionInterval(row, row)
        if self.table.getSelectedRowCount() > 0:
            self.popup.show(e.getComponent(), e.getX(), e.getY())

    def copy_urls(self):
        text = u"\n".join(r.url for r in self.selected_results())
        Toolkit.getDefaultToolkit().getSystemClipboard().setContents(StringSelection(text), None)

    def remove_selected(self):
        idx = [self.table.convertRowIndexToModel(v) for v in self.table.getSelectedRows()]
        self.table.clearSelection()
        self.model.remove_rows(idx)

    # ---- Filtering -------------------------------------------------------
    def matches(self, r):
        sf = self.filterStatus.getSelectedItem()
        if sf == "Errors / no response":
            if r.status != 0:
                return False
        elif sf != "All":
            if r.status // 100 != int(sf[0]):
                return False
        text = self.filterText.getText().strip().lower()
        if text:
            hay = u"%s %s %s %s" % (r.url, r.error, r.status, r.method)
            if text in hay.lower():
                return True
            return self.filterBody.isSelected() and text in r.body_lc(self.helpers)
        return True

    def apply_filter(self):
        self.sorter.setRowFilter(ResultFilter(self))

    # ---- Repeater --------------------------------------------------------
    def send_to_repeater(self):
        rows = [r for r in self.selected_results() if r.request and r.service]
        if not rows:
            JOptionPane.showMessageDialog(self.root, "Select one or more rows with a request first.")
            return
        if len(rows) > CONFIRM_REPEATER_OVER:
            ok = JOptionPane.showConfirmDialog(self.root, "Send %d tabs to Repeater?" % len(rows),
                                               "Confirm", JOptionPane.OK_CANCEL_OPTION)
            if ok != JOptionPane.OK_OPTION:
                return
        for r in rows:
            self.callbacks.sendToRepeater(r.host, r.port, r.https, r.request, "bulk-%d" % r.idx)

    # ---- CSV export ------------------------------------------------------
    @staticmethod
    def _cell(v):
        if isinstance(v, (int, long)):
            return str(v)
        if not isinstance(v, basestring):
            v = unicode(v)
        if isinstance(v, unicode):
            v = v.encode("utf-8")
        if v[:1] in ("=", "+", "-", "@", "\t", "\r"):
            v = "'" + v  # neutralise spreadsheet formula injection
        return v

    def export_csv(self):
        if self.table.getRowCount() == 0:
            JOptionPane.showMessageDialog(self.root, "Nothing to export.")
            return
        fc = JFileChooser()
        if fc.showSaveDialog(self.root) != JFileChooser.APPROVE_OPTION:
            return
        path = fc.getSelectedFile().getAbsolutePath()
        try:
            with open(path, "wb") as f:  # exports what is currently visible, in table order
                w = csv.writer(f)
                w.writerow(ResultsModel.COLS)
                for v in range(self.table.getRowCount()):
                    r = self.model.rows[self.table.convertRowIndexToModel(v)]
                    w.writerow([self._cell(x) for x in
                                (r.idx, r.method, r.url, r.status, r.length, r.ms, r.error)])
        except Exception as e:
            JOptionPane.showMessageDialog(self.root, "Export failed: %s" % e)
            return
        JOptionPane.showMessageDialog(self.root, "Exported %d rows to %s" % (self.table.getRowCount(), path))
