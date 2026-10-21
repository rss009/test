package bulksender;

import javax.swing.*;
import javax.swing.table.AbstractTableModel;
import java.util.ArrayList;
import java.util.List;

/**
 * Table model backing the results grid.
 * All mutations happen on the EDT.
 */
public class ResultsTableModel extends AbstractTableModel {

    private static final String[] COLUMNS =
            {"#", "URL", "Method", "Status", "Length (bytes)", "Time (ms)"};

    private final List<ResultEntry> rows = new ArrayList<>();

    public void addRow(ResultEntry e) {
        SwingUtilities.invokeLater(() -> {
            rows.add(e);
            fireTableRowsInserted(rows.size() - 1, rows.size() - 1);
        });
    }

    public void clear() {
        SwingUtilities.invokeLater(() -> {
            int last = rows.size() - 1;
            if (last >= 0) {
                rows.clear();
                fireTableRowsDeleted(0, last);
            }
        });
    }

    public ResultEntry getEntry(int modelRow) {
        return rows.get(modelRow);
    }

    @Override public int getRowCount()    { return rows.size(); }
    @Override public int getColumnCount() { return COLUMNS.length; }
    @Override public String getColumnName(int col) { return COLUMNS[col]; }

    @Override
    public Class<?> getColumnClass(int col) {
        return switch (col) {
            case 0, 3, 4, 5 -> Integer.class;
            default          -> String.class;
        };
    }

    @Override
    public Object getValueAt(int row, int col) {
        ResultEntry e = rows.get(row);
        return switch (col) {
            case 0 -> e.getIndex();
            case 1 -> e.getUrl();
            case 2 -> e.getMethod();
            case 3 -> e.isError() ? "ERR" : String.valueOf(e.getStatusCode());
            case 4 -> e.isError() ? e.getError() : String.valueOf(e.getResponseLength());
            case 5 -> e.isError() ? "-"   : String.valueOf(e.getResponseTimeMs());
            default -> "";
        };
    }
}
