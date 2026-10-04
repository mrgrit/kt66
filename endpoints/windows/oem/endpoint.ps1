# 이 서비스는 상태만 반환한다. 요청 문자열을 명령·파일 경로로 실행하지 않는다.
$ErrorActionPreference = 'Stop'
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add('http://+:8080/')
$listener.Start()
try {
  while ($listener.IsListening) {
    $context = $listener.GetContext()
    $response = $context.Response
    try {
      if ($context.Request.HttpMethod -ne 'GET' -or $context.Request.Url.AbsolutePath -notin @('/', '/health')) {
        $response.StatusCode = 404
        $body = '{"error":"not_found"}'
      } else {
        $os = Get-CimInstance Win32_OperatingSystem
        $body = @{status='ok'; endpoint='kt66-windows-user'; computer=$env:COMPUTERNAME;
                  operating_system=$os.Caption; version=$os.Version;
                  boot_time=$os.LastBootUpTime.ToUniversalTime().ToString('o');
                  observed_at=[DateTime]::UtcNow.ToString('o');
                  note='Windows guest observation; container running state is separate'} | ConvertTo-Json -Compress
      }
      $bytes = [Text.Encoding]::UTF8.GetBytes($body)
      $response.ContentType = 'application/json; charset=utf-8'
      $response.ContentLength64 = $bytes.Length
      $response.OutputStream.Write($bytes, 0, $bytes.Length)
    } finally { $response.Close() }
  }
} finally { $listener.Stop(); $listener.Close() }
