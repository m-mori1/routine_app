 = Get-Content api.py -Raw
 = .IndexOf('is_spot')
Write-Host .Substring(-40, 160)
