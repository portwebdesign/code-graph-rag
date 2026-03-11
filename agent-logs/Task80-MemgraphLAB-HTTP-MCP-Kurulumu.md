# Task80 - MemgraphLAB HTTP MCP Kurulumu

## Amac

`code-graph-rag` MCP sunucusunu HTTP üzerinden calistirip Memgraph Lab icinden kullanilabilir hale getirmek.

Bu notun odagi, Memgraph Lab Docker icinde calisirken `cgr` MCP HTTP sunucusuna nasil baglanacagini netlestirmektir.

## Temel Bulgular

1. `127.0.0.1` veya `localhost` Memgraph Lab konteyneri icinden host makineyi degil, konteynerin kendisini isaret eder.
2. Bu nedenle Memgraph Lab tarafinda `http://127.0.0.1:8765/mcp` kullanmak yanlistir.
3. Dogru erisim adresi `host.docker.internal` uzerinden verilmelidir.
4. `code-graph-rag` MCP sunucusu sadece `127.0.0.1` uzerinde degil, dis arayuze de acik olacak sekilde `0.0.0.0` host'u ile baslatilmalidir.
5. Memgraph Lab tarafinda kullanilacak URL `http://host.docker.internal:8765/mcp` olmalidir.

## Gerekli Terminal Komutu

Asagidaki komut Windows host tarafinda calistirilmalidir:

```powershell
cd D:\PROGRAMMING\code-graph-rag
$env:TARGET_REPO_PATH = "D:\PROGRAMMING\abey"
D:\PROGRAMMING\code-graph-rag\.venv\Scripts\python.exe -m codebase_rag.cli mcp-server --transport http --host 0.0.0.0 --port 8765 --path /mcp
```

Notlar:

1. `--host 0.0.0.0` zorunlu. Aksi halde sadece host icindeki loopback'te dinler.
2. `--path /mcp` kullanilmali. Memgraph Lab baglantisi kok URL'ye degil bu path'e gitmeli.
3. Windows'ta `uv run cgr ...` bazen launcher kilidine takilabildigi icin en guvenli secenek `python -m codebase_rag.cli ...` komutudur.

## Memgraph Lab Ayarlari

Memgraph Lab baglantisi olusturulurken su degerler girilmelidir:

### MCP Server URL

```text
http://host.docker.internal:8765/mcp
```

### MCP Transport Type

```text
Streamable HTTP
```

### Access Token

Bos birakilabilir.

Mevcut implementasyonda `MCP_HTTP_AUTH_TOKEN` varsayilan olarak bos oldugu icin token zorunlu degildir.

### Additional Headers

Bos birakilabilir.

## Calisan Dogrulama Modeli

Asagidaki senaryo calisan durum olarak kabul edilir:

1. Terminalde sunucu `Uvicorn running on http://0.0.0.0:8765` benzeri log verir.
2. Host makinede `http://127.0.0.1:8765/health` cagrisina cevap gelir.
3. Browser-benzeri `POST /mcp` initialize istegi `200` doner.
4. Yanitta `Mcp-Session-Id` header'i bulunur.
5. Memgraph Lab baglanti formunda `http://host.docker.internal:8765/mcp` ile baglanti basarili olur.

## Neden `127.0.0.1` Calismadi

Memgraph Lab Docker icinde calistigi icin:

1. `127.0.0.1:8765` ifadesi konteynerin kendi loopback adresine gider.
2. Host makinede ayakta duran `code-graph-rag` MCP sunucusuna ulasamaz.
3. Bu da Memgraph Lab tarafinda `fetch failed` veya `500 Internal Server Error` ile gorunur.

## Neden `host.docker.internal` Calisti

Bu alias Docker konteyneri icinden host makineye doner. Bu ortamda `host.docker.internal` su IP'ye cozuldu:

```text
192.168.1.41
```

Bu sayede Memgraph Lab konteyneri host uzerindeki MCP HTTP sunucusuna ulasabildi.

## Hizli Kontrol Komutlari

Sunucu ayakta mi:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Tool katalogu geliyor mu:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/tools
```

Standart MCP initialize testi:

```powershell
$headers = @{ Accept = 'application/json, text/event-stream' }
$body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"manual-test","version":"1.0.0"}}}'
Invoke-WebRequest http://127.0.0.1:8765/mcp -Method Post -Headers $headers -Body $body -ContentType 'application/json'
```

## Sonuc

Memgraph Lab icin calisan kombinasyon su sekildedir:

1. Host tarafinda `code-graph-rag` MCP sunucusunu `--transport http --host 0.0.0.0 --port 8765 --path /mcp` ile baslat.
2. Memgraph Lab tarafinda `http://host.docker.internal:8765/mcp` adresini kullan.
3. Transport tipini `Streamable HTTP` sec.

Bu konfigurasyonla `cgr` MCP araclari Memgraph Lab icinde HTTP uzerinden kullanilabilir hale gelir.
