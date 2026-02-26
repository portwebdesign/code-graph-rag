# JetBrains IDE MCP Setup for Code-Graph-RAG

## Problem
MCP sunucusu stdio bağlantısı kuramadan kapanıyor:
```
MCP error -32000: Connection closed
anyio.WouldBlock
```

## Solution

### 1. MCP Sunucusunu Test Edin

Önce sunucunun düzgün çalıştığını doğrulayın:

```powershell
# Terminal'de çalıştırın
cd D:\PROGRAMMING\code-graph-rag
$env:TARGET_REPO_PATH = "D:\PROGRAMMING\code-graph-rag"
python -m codebase_rag.cli mcp-server
```

Şu çıktıyı görmelisiniz:
```
[GraphCode MCP] Starting MCP server...
[GraphCode MCP] Connected to Memgraph at localhost:7687
```

Sunucu stdin'den mesaj bekleyecek (normal davranış). Ctrl+C ile durdurun.

### 2. JetBrains Copilot MCP Konfigürasyonu

JetBrains IDE'de MCP sunucusu eklemek için:

#### Option A: Settings UI
1. **Settings** → **Tools** → **GitHub Copilot** → **Model Context Protocol**
2. **Add Server** butonuna tıklayın
3. Şu bilgileri girin:

**Server Name:** `code-graph-rag`

**Command:**
```
python
```

**Arguments:**
```
-m
codebase_rag.cli
mcp-server
```

**Working Directory:**
```
D:\PROGRAMMING\code-graph-rag
```

**Environment Variables:**
```
TARGET_REPO_PATH=D:\PROGRAMMING\code-graph-rag
CYPHER_PROVIDER=ollama
CYPHER_MODEL=codellama
ORCHESTRATOR_PROVIDER=ollama
ORCHESTRATOR_MODEL=llama3.2
```

#### Option B: JSON Configuration (Advanced)

Eğer settings dosyası destekliyorsa, şu JSON'u kullanın:

```json
{
  "mcpServers": {
    "code-graph-rag": {
      "command": "python",
      "args": ["-m", "codebase_rag.cli", "mcp-server"],
      "cwd": "D:\\PROGRAMMING\\code-graph-rag",
      "env": {
        "TARGET_REPO_PATH": "D:\\PROGRAMMING\\code-graph-rag",
        "CYPHER_PROVIDER": "ollama",
        "CYPHER_MODEL": "codellama",
        "ORCHESTRATOR_PROVIDER": "ollama",
        "ORCHESTRATOR_MODEL": "llama3.2"
      }
    }
  }
}
```

### 3. Python Interpreter Ayarı

JetBrains IDE doğru Python interpreter'ı kullanmalı:

1. **Settings** → **Project** → **Python Interpreter**
2. Virtual environment seçin: `D:\PROGRAMMING\my-code-graph-rag\.venv`
3. Veya system Python kullanın (uv yüklü olmalı)

### 4. Windows Specific Issues

#### Path Separators
Windows'ta path'lerde backslash (`\`) veya forward slash (`/`) kullanın:
- ✅ `D:/PROGRAMMING/code-graph-rag`
- ✅ `D:\\PROGRAMMING\\code-graph-rag`
- ❌ `D:\PROGRAMMING\code-graph-rag` (JSON'da escape edilmeli)

#### UV ile Çalıştırma
Eğer `uv` kullanmak istiyorsanız:

**Command:**
```
uv
```

**Arguments:**
```
run
cgr
mcp-server
```

### 5. Memgraph Bağlantısı

MCP sunucusu Memgraph'e bağlanmaya çalışır. Docker container'ın çalıştığından emin olun:

```powershell
docker-compose up -d
```

Kontrol:
```powershell
docker ps | Select-String memgraph
```

### 6. Debug Logs

MCP sunucusu loglarını görmek için:

```powershell
# Terminal'de çalıştırın
cd D:\PROGRAMMING\code-graph-rag
$env:TARGET_REPO_PATH = "D:\PROGRAMMING\code-graph-rag"
$env:LOGURU_LEVEL = "DEBUG"
python -m codebase_rag.cli mcp-server 2>&1 | Tee-Object -FilePath mcp-debug.log
```

### 7. Available MCP Tools

Sunucu başarıyla bağlandığında şu toollar kullanılabilir:

- `index_repository` - Repository'yi parse et ve graph'a yükle
- `query_code_graph` - Natural language Cypher query
- `get_code_snippet` - Kod snippet'i getir
- `surgical_replace_code` - Kod düzenle
- `read_file` - Dosya oku
- `write_file` - Dosya yaz
- `list_directory` - Klasör listele
- `list_projects` - Indexlenmiş projeleri listele
- `delete_project` - Proje sil
- `wipe_database` - Tüm veritabanını temizle

### 8. Testing the Connection

JetBrains Copilot'ta test edin:

```
@code-graph-rag list all indexed projects
@code-graph-rag index this repository
@code-graph-rag what functions are in this codebase?
```

## Troubleshooting

### Error: "Connection closed immediately"
- Python interpreter yolu doğru mu?
- Working directory doğru mu?
- Memgraph çalışıyor mu?

### Error: "Module not found: codebase_rag"
- Python interpreter virtual environment içinde mi?
- `uv sync` çalıştırıldı mı?

### Error: "Cannot connect to Memgraph"
- Docker container çalışıyor mu?
- Port 7687 kullanılabilir mi?
- Firewall engelliyor olabilir mi?

### Error: "TARGET_REPO_PATH not set"
- Environment variable eklenmiş mi?
- Absolute path kullanılmış mı?

## Alternative: VS Code MCP

VS Code veya Claude Desktop kullanıyorsanız:

**settings.json** (VS Code):
```json
{
  "mcp.servers": {
    "code-graph-rag": {
      "command": "uv",
      "args": ["run", "--directory", "D:/PROGRAMMING/code-graph-rag", "cgr", "mcp-server"],
      "env": {
        "TARGET_REPO_PATH": "D:/PROGRAMMING/code-graph-rag"
      }
    }
  }
}
```

**claude_desktop_config.json** (Claude Desktop):
```json
{
  "mcpServers": {
    "code-graph-rag": {
      "command": "uv",
      "args": ["run", "--directory", "D:/PROGRAMMING/code-graph-rag", "cgr", "mcp-server"],
      "env": {
        "TARGET_REPO_PATH": "D:/PROGRAMMING/code-graph-rag"
      }
    }
  }
}
```

## Support

- GitHub: https://github.com/vitali87/code-graph-rag
- Docs: `docs/claude-code-setup.md`
- Issues: Report on GitHub
