# URL Bulk Sender — Burp Suite Extension

A Burp Suite extension that lets you paste (or load from file) a list of
URLs, choose an HTTP method, fire every request through Burp's engine, and
inspect results in an Intruder-style table.

## Features

| Feature | Details |
|---------|---------|
| **URL list** | Paste URLs directly or load from a `.txt` file (one URL per line, `#` comments ignored) |
| **Method picker** | GET · POST · PUT · DELETE · PATCH · HEAD · OPTIONS · TRACE |
| **Extra headers** | Per-run custom headers appended to every request |
| **Request body** | Optional body for POST/PUT/PATCH (auto-sets `Content-Length`) |
| **Concurrency** | Configurable thread pool (1–100 threads) |
| **Timeout** | Per-request timeout (1–600 seconds) |
| **Results table** | Sortable columns: `#` · URL · Method · **Status** · **Length (bytes)** · **Time (ms)** |
| **Status colouring** | 2xx green · 3xx yellow · 4xx red · 5xx orange · errors grey |
| **Full message view** | Click any row → Burp's native request/response editors update instantly |
| **Stop mid-run** | Stop button terminates in-flight pool gracefully |

## Build

Requires Java 17+ and Gradle.

```bash
gradle jar
# → build/libs/url-bulk-sender.jar
```

## Install in Burp

1. Burp → **Extensions** → **Add**
2. Extension type: **Java**
3. Select `build/libs/url-bulk-sender.jar`
4. The **Bulk Sender** tab appears in the Burp suite toolbar.

## Usage

1. Paste URLs into the **URLs** box (or click *Load URLs from file…*).
2. Select the HTTP **Method**.
3. Optionally add extra headers or a request body.
4. Set **Threads** and **Timeout**.
5. Click **▶ Start**.
6. Watch results populate in the table in real time.
7. Click any row to see the full raw request and response in the editors below.

## Source layout

```
src/main/java/bulksender/
  BulkSenderExtension.java   – extension entry-point & UI
  ResultEntry.java           – single result POJO
  ResultsTableModel.java     – Swing table model
  StatusCellRenderer.java    – colour-coded status column
```
