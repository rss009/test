package bulksender;

import burp.api.montoya.http.message.HttpRequestResponse;

/**
 * Holds one completed request/response pair plus timing/status metadata
 * so the table model can display it without touching Burp's HTTP objects again.
 */
public class ResultEntry {

    private final int index;
    private final String url;
    private final String method;
    private final int statusCode;
    private final long responseTimeMs;
    private final long responseLength;
    private final String error;
    private final HttpRequestResponse requestResponse;

    /** Successful result */
    public ResultEntry(int index, String url, String method,
                       int statusCode, long responseTimeMs, long responseLength,
                       HttpRequestResponse requestResponse) {
        this.index          = index;
        this.url            = url;
        this.method         = method;
        this.statusCode     = statusCode;
        this.responseTimeMs = responseTimeMs;
        this.responseLength = responseLength;
        this.error          = null;
        this.requestResponse = requestResponse;
    }

    /** Error result (no HTTP response received) */
    public ResultEntry(int index, String url, String method, String error) {
        this.index          = index;
        this.url            = url;
        this.method         = method;
        this.statusCode     = -1;
        this.responseTimeMs = -1;
        this.responseLength = -1;
        this.error          = error;
        this.requestResponse = null;
    }

    public int getIndex()             { return index; }
    public String getUrl()            { return url; }
    public String getMethod()         { return method; }
    public int getStatusCode()        { return statusCode; }
    public long getResponseTimeMs()   { return responseTimeMs; }
    public long getResponseLength()   { return responseLength; }
    public String getError()          { return error; }
    public boolean isError()          { return error != null; }
    public HttpRequestResponse getRequestResponse() { return requestResponse; }
}
