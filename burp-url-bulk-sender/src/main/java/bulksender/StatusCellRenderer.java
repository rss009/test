package bulksender;

import javax.swing.*;
import javax.swing.table.DefaultTableCellRenderer;
import java.awt.*;

/**
 * Colours the Status column by HTTP status class, similar to Burp Intruder.
 */
public class StatusCellRenderer extends DefaultTableCellRenderer {

    private static final Color C_2XX  = new Color(0xC8E6C9); // green-ish
    private static final Color C_3XX  = new Color(0xFFF9C4); // yellow
    private static final Color C_4XX  = new Color(0xFFCDD2); // red
    private static final Color C_5XX  = new Color(0xFFCC80); // orange
    private static final Color C_ERR  = new Color(0xE0E0E0); // grey

    @Override
    public Component getTableCellRendererComponent(
            JTable table, Object value, boolean isSelected,
            boolean hasFocus, int row, int column) {

        super.getTableCellRendererComponent(table, value, isSelected, hasFocus, row, column);
        setHorizontalAlignment(CENTER);

        if (!isSelected) {
            String text = value == null ? "" : value.toString();
            try {
                int code = Integer.parseInt(text);
                if      (code >= 200 && code < 300) setBackground(C_2XX);
                else if (code >= 300 && code < 400) setBackground(C_3XX);
                else if (code >= 400 && code < 500) setBackground(C_4XX);
                else if (code >= 500 && code < 600) setBackground(C_5XX);
                else                                 setBackground(table.getBackground());
            } catch (NumberFormatException ex) {
                setBackground(C_ERR);
            }
        } else {
            setBackground(table.getSelectionBackground());
        }

        return this;
    }
}
