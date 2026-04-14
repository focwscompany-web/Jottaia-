"""
jotta_telegram.py v5.0 — Jotta.ia
- Configurações salvas em jotta_config.json (nunca se perdem)
- Atualizações preservam configs automaticamente
- Fallback Groq → Gemini
- Linguagem natural → shell
- Áudio, imagens, cotações, notas
"""

from telethon import TelegramClient, events
import asyncio, httpx, json, os, base64, sys
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────
# CONFIGURAÇÃO — lida do arquivo externo
# ─────────────────────────────────────────

CONFIG_FILE = Path.home() / "jotta_config.json"
NOTAS_FILE  = Path.home() / "jotta_notas.json"
SCRIPT_PATH = Path.home() / "jotta_telegram.py"

def carregar_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    # Config padrão se não existir
    cfg = {
        "telegram_api_id": 0,
        "telegram_api_hash": "",
        "usuario_autorizado": 0,
        "groq_api_key": "",
        "gemini_api_key": "",
        "openrouter_api_key": "",
        "modelo_groq": "llama-3.3-70b-versatile",
        "modelo_gemini": "gemini-1.5-flash",
        "sistema_prompt": "Você é Jotta.ia, assistente pessoal do Jotta. Converse de forma natural e direta em português brasileiro."
    }
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return cfg

def salvar_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

cfg = carregar_config()

TELEGRAM_API_ID    = cfg["telegram_api_id"]
TELEGRAM_API_HASH  = cfg["telegram_api_hash"]
USUARIO_AUTORIZADO = cfg["usuario_autorizado"]
GROQ_API_KEY       = cfg["groq_api_key"]
GEMINI_API_KEY     = cfg["gemini_api_key"]
OPENROUTER_API_KEY = cfg["openrouter_api_key"]
SISTEMA_PROMPT     = cfg["sistema_prompt"]

MODELOS = {
    "groq":       cfg["modelo_groq"],
    "gemini":     cfg["modelo_gemini"],
}

SHELL_PROMPT = (
    "Converta o pedido abaixo em UM comando shell para Termux/Android/Linux.\n"
    "REGRAS:\n"
    "1. Responda SOMENTE o comando, nada mais.\n"
    "2. Sem explicações, sem markdown, sem aspas, sem comentários.\n"
    "3. Se for conversa social (oi, tudo bem, obrigado, etc), responda: NAO_E_COMANDO\n"
    "4. Se for pergunta que precisa de resposta de texto, responda: NAO_E_COMANDO\n\n"
    "EXEMPLOS:\n"
    "qual meu espaço livre -> df -h\n"
    "lista arquivos -> ls -la ~\n"
    "qual meu ip -> curl -s ifconfig.me\n"
    "processos rodando -> ps aux | head -20\n"
    "atualiza pacotes -> pkg upgrade -y\n"
    "uso de memória -> free -h\n"
    "oi tudo bem -> NAO_E_COMANDO\n"
    "o que é python -> NAO_E_COMANDO\n\n"
    "Pedido: {pedido}\nComando:"
)

# ─────────────────────────────────────────
# NOTAS
# ─────────────────────────────────────────

def carregar_notas() -> list:
    if NOTAS_FILE.exists():
        return json.loads(NOTAS_FILE.read_text())
    return []

def salvar_notas(notas: list):
    NOTAS_FILE.write_text(json.dumps(notas, ensure_ascii=False, indent=2))

def adicionar_nota(texto: str) -> str:
    notas = carregar_notas()
    nota = {"id": len(notas) + 1, "texto": texto, "data": datetime.now().strftime("%d/%m/%Y %H:%M")}
    notas.append(nota)
    salvar_notas(notas)
    return f"✅ Nota #{nota['id']} salva!"

def listar_notas() -> str:
    notas = carregar_notas()
    if not notas:
        return "📝 Nenhuma nota salva."
    linhas = ["📝 Suas notas:\n"]
    for n in notas:
        linhas.append(f"#{n['id']} — {n['data']}\n{n['texto']}\n")
    return "\n".join(linhas)

def apagar_nota(id: int) -> str:
    notas = carregar_notas()
    novas = [n for n in notas if n["id"] != id]
    if len(novas) == len(notas):
        return f"❌ Nota #{id} não encontrada."
    salvar_notas(novas)
    return f"🗑️ Nota #{id} apagada."

# ─────────────────────────────────────────
# COTAÇÕES
# ─────────────────────────────────────────

ALIASES = {
    "bitcoin": "BTC-USD", "btc": "BTC-USD",
    "ethereum": "ETH-USD", "eth": "ETH-USD",
    "dolar": "BRL=X", "usd": "BRL=X",
    "euro": "EURBRL=X", "eur": "EURBRL=X",
    "petr4": "PETR4.SA", "vale3": "VALE3.SA",
    "ibov": "^BVSP", "sp500": "^GSPC",
    "solana": "SOL-USD", "sol": "SOL-USD",
    "bnb": "BNB-USD",
}

async def buscar_cotacao(simbolo: str) -> str:
    sym = ALIASES.get(simbolo.lower(), simbolo.upper())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=headers)
            meta = r.json()["chart"]["result"][0]["meta"]
            preco = meta.get("regularMarketPrice", 0)
            anterior = meta.get("chartPreviousClose", preco)
            variacao = ((preco - anterior) / anterior * 100) if anterior else 0
            moeda = meta.get("currency", "")
            seta = "🟢▲" if variacao >= 0 else "🔴▼"
            return f"📊 {sym}\nPreço: {preco:,.2f} {moeda}\n{seta} {variacao:+.2f}% hoje"
    except:
        return f"❌ Não encontrei cotação para {simbolo}."

# ─────────────────────────────────────────
# SHELL
# ─────────────────────────────────────────

async def executar_shell(comando: str) -> str:
    timeout = 30
    palavras_longas = ["install", "upgrade", "download", "proot-distro", "pip install", "apt", "pkg upgrade", "clone"]
    if any(p in comando for p in palavras_longas):
        timeout = 600
    try:
        proc = await asyncio.create_subprocess_shell(
            comando,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if timeout > 30:
            await client.send_message(USUARIO_AUTORIZADO, "⏳ Executando comando longo, aguarde...")
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        saida = stdout.decode("utf-8", errors="replace").strip()
        erro  = stderr.decode("utf-8", errors="replace").strip()
        resultado = saida or erro or "✅ Executado (sem saída)"
        if len(resultado) > 3500:
            resultado = resultado[:3500] + "\n...(saída cortada)"
        return resultado
    except asyncio.TimeoutError:
        return f"⏱️ Timeout — comando demorou mais de {timeout}s"
    except Exception as e:
        return f"❌ Erro: {e}"

# ─────────────────────────────────────────
# ÁUDIO
# ─────────────────────────────────────────

async def transcrever_audio(caminho: str) -> str:
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        with open(caminho, "rb") as f:
            audio_bytes = f.read()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                url, headers=headers,
                files={"file": ("audio.ogg", audio_bytes, "audio/ogg")},
                data={"model": "whisper-large-v3", "language": "pt"},
            )
            r.raise_for_status()
            return r.json().get("text", "")
    except Exception as e:
        return f"[erro: {e}]"

# ─────────────────────────────────────────
# IMAGEM
# ─────────────────────────────────────────

async def analisar_imagem(caminho: str, pergunta: str) -> str:
    with open(caminho, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    ext = caminho.split(".")[-1].lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"inline_data": {"mime_type": mime, "data": img_b64}}, {"text": pergunta}]}]}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, json=payload)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"❌ Erro ao analisar imagem: {e}"

# ─────────────────────────────────────────
# PROVEDORES IA
# ─────────────────────────────────────────

async def _groq(msgs: list) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, headers=headers, json={"model": MODELOS["groq"], "messages": msgs, "max_tokens": 1024})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def _gemini(msgs: list) -> str:
    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELOS['gemini']}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

async def _deepseek(msgs: list) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://jotta.ia",
        "X-Title": "Jotta.ia",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, headers=headers, json={
            "model": "deepseek/deepseek-r1:free",
            "messages": msgs,
            "max_tokens": 1024,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def _mistral(msgs: list) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://jotta.ia",
        "X-Title": "Jotta.ia",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, headers=headers, json={
            "model": "mistralai/mistral-small-3.1-24b-instruct:free",
            "messages": msgs,
            "max_tokens": 1024,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def _qwen(msgs: list) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://jotta.ia",
        "X-Title": "Jotta.ia",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, headers=headers, json={
            "model": "qwen/qwen2.5-72b-instruct:free",
            "messages": msgs,
            "max_tokens": 1024,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def _gemini_pro(msgs: list) -> str:
    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

async def _deepseek_v3(msgs: list) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://jotta.ia",
        "X-Title": "Jotta.ia",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, headers=headers, json={
            "model": "deepseek/deepseek-chat-v3-0324:free",
            "messages": msgs,
            "max_tokens": 1024,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def _claude_haiku(msgs: list) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://jotta.ia",
        "X-Title": "Jotta.ia",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, headers=headers, json={
            "model": "anthropic/claude-3-haiku",
            "messages": msgs,
            "max_tokens": 1024,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

PROVEDORES = [
    ("Groq", _groq),
    ("DeepSeek R1", _deepseek),
    ("DeepSeek V3", _deepseek_v3),
    ("Mistral", _mistral),
    ("Qwen 2.5", _qwen),
    ("Gemini Flash", _gemini),
    ("Gemini Pro", _gemini_pro),
    ("Claude Haiku", _claude_haiku),
]

# Tarefas que precisam de colaboração de todas as IAs
TAREFAS_COLABORATIVAS = [
    "cria", "crie", "faz", "faça", "desenvolve", "desenvolva",
    "copia", "copie", "clone", "clona", "site", "website", "app",
    "aplicativo", "sistema", "programa", "código", "script",
    "landing page", "dashboard", "api", "bot", "projeto"
]
provedor_atual = 0

async def perguntar_ia(texto: str, system: str = None) -> tuple:
    global provedor_atual
    msgs = [
        {"role": "system", "content": system or SISTEMA_PROMPT},
        {"role": "user", "content": texto},
    ]
    ordem = list(range(provedor_atual, len(PROVEDORES))) + list(range(0, provedor_atual))
    for i in ordem:
        nome, func = PROVEDORES[i]
        try:
            resposta = await func(msgs)
            provedor_atual = i
            return resposta.strip(), nome
        except Exception as e:
            print(f"[{nome}] falhou: {e}")
    return "❌ Todos os provedores falharam.", "nenhum"

async def perguntar_multi_agente(texto: str, system: str = None) -> str:
    """Consulta todas as IAs e combina as respostas."""
    msgs = [
        {"role": "system", "content": system or SISTEMA_PROMPT},
        {"role": "user", "content": texto},
    ]
    respostas = []
    for nome, func in PROVEDORES:
        try:
            r = await func(msgs)
            if r and len(r) > 10:
                respostas.append((nome, r.strip()))
        except:
            pass
    if not respostas:
        return "❌ Todos os provedores falharam."
    if len(respostas) == 1:
        return respostas[0][1]
    # Combina as respostas usando Groq
    combinado = "\n\n".join([f"[{n}]: {r[:500]}" for n, r in respostas])
    prompt_combinar = (
        f"Você recebeu respostas de {len(respostas)} IAs diferentes sobre a mesma pergunta.\n"
        f"Combine o melhor de cada resposta em uma única resposta clara e completa em português:\n\n"
        f"{combinado}"
    )
    try:
        final, _ = await perguntar_ia(prompt_combinar)
        nomes = " + ".join([n for n, _ in respostas])
        return f"{final}\n\n_via {nomes}_"
    except:
        return respostas[0][1]

async def ia_para_shell(pedido: str) -> str:
    prompt = SHELL_PROMPT.format(pedido=pedido)
    resposta, _ = await perguntar_ia(prompt, system="Você converte pedidos em comandos shell. Responda APENAS com o comando ou NAO_E_COMANDO.")
    cmd = resposta.strip().strip("`").split("\n")[0].strip()
    return cmd

# ─────────────────────────────────────────
# ATUALIZAÇÃO AUTOMÁTICA
# ─────────────────────────────────────────

async def atualizar_script(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url)
            r.raise_for_status()
            novo = r.text
        # Verifica se é um script válido
        if "TelegramClient" not in novo:
            return "❌ Arquivo inválido — não parece ser o script correto."
        SCRIPT_PATH.write_text(novo)
        return "✅ Script atualizado! As configurações estão salvas em jotta_config.json. Reiniciando..."
    except Exception as e:
        return f"❌ Falha na atualização: {e}"

# ─────────────────────────────────────────
# CLIENTE TELEGRAM
# ─────────────────────────────────────────

client = TelegramClient("jotta_session", TELEGRAM_API_ID, TELEGRAM_API_HASH)

@client.on(events.NewMessage(from_users=USUARIO_AUTORIZADO))
async def handler(event):
    global provedor_atual, cfg, GROQ_API_KEY, GEMINI_API_KEY, SISTEMA_PROMPT
    texto = (event.text or "").strip()
    msg = event.message

    # 🎙️ ÁUDIO
    if msg.voice or msg.audio:
        await event.reply("🎙️ Transcrevendo...")
        caminho = await client.download_media(msg, file="/tmp/jotta_audio.ogg")
        transcricao = await transcrever_audio(caminho)
        if transcricao.startswith("[erro"):
            await event.reply(f"❌ {transcricao}")
            return
        await event.reply(f"🎙️ Você disse: {transcricao}")
        cmd = await ia_para_shell(transcricao)
        if cmd and cmd != "NAO_E_COMANDO" and len(cmd) < 300:
            await event.reply(f"⚙️ `{cmd}`")
            saida = await executar_shell(cmd)
            await event.reply(f"```\n{saida}\n```")
        else:
            resposta, _ = await perguntar_ia(transcricao)
            await event.reply(resposta)
        return

    # 🖼️ IMAGEM
    if msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
        await event.reply("🖼️ Analisando...")
        caminho = await client.download_media(msg, file="/tmp/jotta_imagem.jpg")
        pergunta = texto if texto else "O que você vê nessa imagem? Descreva em português."
        analise = await analisar_imagem(caminho, pergunta)
        await event.reply(analise)
        return

    if not texto:
        return

    # /atualizar
    if texto.startswith("/atualizar "):
        url = texto.split()[1]
        await event.reply("🔄 Atualizando script...")
        resultado = await atualizar_script(url)
        await event.reply(resultado)
        if "Reiniciando" in resultado:
            await asyncio.sleep(1)
            os.execv(sys.executable, [sys.executable, str(SCRIPT_PATH)])
        return

    # /config — ver ou mudar configurações
    if texto.startswith("/config"):
        partes = texto.split(maxsplit=2)
        if len(partes) == 1:
            cfg_atual = carregar_config()
            await event.reply(
                f"⚙️ **Configurações atuais:**\n\n"
                f"groq_api_key: ...{cfg_atual['groq_api_key'][-8:]}\n"
                f"gemini_api_key: ...{cfg_atual['gemini_api_key'][-8:]}\n"
                f"modelo_groq: {cfg_atual['modelo_groq']}\n"
                f"modelo_gemini: {cfg_atual['modelo_gemini']}\n\n"
                f"Use `/config chave valor` para alterar.\n"
                f"Ex: `/config groq_api_key SUA_CHAVE`"
            )
        elif len(partes) == 3:
            chave, valor = partes[1], partes[2]
            cfg_atual = carregar_config()
            if chave in cfg_atual:
                cfg_atual[chave] = valor
                salvar_config(cfg_atual)
                # Atualiza em memória
                if chave == "groq_api_key": GROQ_API_KEY = valor
                if chave == "gemini_api_key": GEMINI_API_KEY = valor
                if chave == "sistema_prompt": SISTEMA_PROMPT = valor
                await event.reply(f"✅ `{chave}` atualizado e salvo!")
            else:
                await event.reply(f"❌ Chave `{chave}` não existe.")
        return

    # /cotacao
    if texto.startswith("/cotacao"):
        partes = texto.split()
        if len(partes) < 2:
            await event.reply("Ex: /cotacao btc | /cotacao dolar | /cotacao PETR4")
            return
        await event.reply(await buscar_cotacao(partes[1]))
        return

    # /nota
    if texto.startswith("/nota "):
        await event.reply(adicionar_nota(texto[6:].strip()))
        return

    if texto == "/notas":
        await event.reply(listar_notas())
        return

    if texto.startswith("/apagarnota"):
        partes = texto.split()
        if len(partes) == 2 and partes[1].isdigit():
            await event.reply(apagar_nota(int(partes[1])))
        return

    # /run
    if texto.startswith("/run "):
        cmd = texto[5:].strip()
        await event.reply(f"⚙️ `{cmd}`")
        saida = await executar_shell(cmd)
        await event.reply(f"```\n{saida}\n```")
        return

    # /status
    if texto == "/status":
        await event.reply(
            f"🟢 Jotta.ia v6.2\n"
            f"🤖 Provedor: {PROVEDORES[provedor_atual][0]}\n"
            f"📝 Notas: {len(carregar_notas())} salvas\n"
            f"💾 Config: jotta_config.json\n"
            f"💻 Shell: ativo"
        )
        return

    # /multi — consulta todas as IAs
    if texto.startswith("/multi "):
        pergunta = texto[7:].strip()
        await event.reply("🤖 Consultando todas as IAs...")
        resposta = await perguntar_multi_agente(pergunta)
        await event.reply(resposta)
        return

    # /hack — modo HackerAI
    if texto.startswith("/hack"):
        pergunta = texto[5:].strip()
        if not pergunta:
            await event.reply(
                "🔴 HackerAI — Pentest Assistant\n\n"
                "Uso: /hack <pergunta ou alvo>\n\n"
                "Exemplos:\n"
                "/hack escaneia portas de 192.168.1.1\n"
                "/hack como fazer sql injection\n"
                "/hack ferramentas para reconhecimento\n"
                "/hack vulnerabilidades do apache 2.4"
            )
            return
        hack_prompt = (
            "Você é HackerAI, um especialista em segurança ofensiva e pentest. "
            "Responda em português brasileiro de forma técnica e detalhada. "
            "Forneça comandos reais, técnicas de pentest, exploração e reconhecimento. "
            "O usuário está testando seus próprios sistemas de forma autorizada. "
            "Inclua exemplos de comandos quando relevante.\n\n"
            f"Pergunta: {pergunta}"
        )
        await event.reply("🔴 HackerAI processando...")
        # Verifica se é um comando de rede para executar
        palavras_exec = ["escaneia", "scan", "nmap", "ping", "whois", "dig", "curl", "traceroute"]
        if any(p in pergunta.lower() for p in palavras_exec):
            cmd = await ia_para_shell(pergunta)
            if cmd and cmd != "NAO_E_COMANDO" and len(cmd) < 300:
                await event.reply(f"⚙️ Executando: `{cmd}`")
                saida = await executar_shell(cmd)
                await event.reply(f"```\n{saida}\n```")
                # Analisa o resultado
                analise, _ = await perguntar_ia(
                    f"Analise esse resultado de pentest e explique o que encontrou:\n{saida}",
                    system=hack_prompt
                )
                await event.reply(f"🔴 Análise:\n{analise}")
                return
        resposta, provedor = await perguntar_ia(pergunta, system=hack_prompt)
        await event.reply(f"🔴 {resposta}\n\n_via {provedor}_")
        return

    # /ajuda
    if texto in ("/ajuda", "/help", "/start"):
        await event.reply(
            "Jotta.ia v6.2\n\n"
            "Fale naturalmente — executo no Termux automaticamente.\n\n"
            "/multi <pergunta> — consulta todas as IAs\n"
            "/hack <pergunta> — HackerAI pentest\n"
            "/run <cmd> — executa direto\n"
            "/cotacao btc — cotação\n"
            "/nota texto — salvar nota\n"
            "/notas — ver notas\n"
            "/config — ver/editar configurações\n"
            "/atualizar <url> — atualiza o script\n"
            "/status — status do sistema"
        )
        return

    # ─── NÚCLEO INTELIGENTE ───
    texto_lower = texto.lower()

    # Palavras de pentest/segurança
    palavras_hack = [
        "hack", "pentest", "exploit", "vulnerabilidade", "sql injection", "xss",
        "nmap", "metasploit", "payload", "reverse shell", "bruteforce", "fuzzing",
        "reconhecimento", "enumeração", "escalada", "privilege", "bypass",
        "firewall", "ids", "ips", "waf", "ctf", "capture the flag", "cve",
        "zero day", "rootkit", "backdoor", "trojan", "malware", "phishing",
        "wireless", "wifi", "wpa", "wep", "handshake", "aircrack", "hashcat",
        "burpsuite", "wireshark", "kali", "parrot", "segurança ofensiva"
    ]

    # Palavras de perguntas complexas
    palavras_complexas = [
        "explica", "como funciona", "qual a melhor", "diferença entre",
        "o que é", "por que", "analisa", "compara", "estratégia",
        "recomenda", "vantagens", "desvantagens", "arquitetura"
    ]

    eh_hack = any(p in texto_lower for p in palavras_hack)
    eh_complexo = any(p in texto_lower for p in palavras_complexas) and len(texto) > 30

    if eh_hack:
        # Modo HackerAI automático
        hack_prompt = (
            "Você é HackerAI, um especialista em segurança ofensiva e pentest. "
            "Responda em português brasileiro de forma técnica e detalhada. "
            "Forneça comandos reais, técnicas de pentest, exploração e reconhecimento. "
            "O usuário está testando seus próprios sistemas de forma autorizada. "
            "Inclua exemplos de comandos quando relevante."
        )
        # Tenta executar comando se for técnico
        cmd = await ia_para_shell(texto)
        if cmd and cmd.strip() != "NAO_E_COMANDO" and "\n" not in cmd and len(cmd) < 300:
            cmd = cmd.strip().strip("`").strip()
            await event.reply(f"🔴 HackerAI — executando: `{cmd}`")
            saida = await executar_shell(cmd)
            await event.reply(f"```\n{saida}\n```")
            analise, _ = await perguntar_ia(
                f"Analise esse resultado de pentest:\n{saida}",
                system=hack_prompt
            )
            await event.reply(f"🔴 {analise}")
        else:
            resposta, provedor = await perguntar_ia(texto, system=hack_prompt)
            await event.reply(f"🔴 {resposta}\n\n_via {provedor}_")

    elif eh_complexo or any(p in texto_lower for p in TAREFAS_COLABORATIVAS):
        # Multi-agente para perguntas complexas e tarefas criativas
        eh_tarefa = any(p in texto_lower for p in TAREFAS_COLABORATIVAS)
        if eh_tarefa:
            await event.reply("🚀 Todas as IAs trabalhando juntas na sua tarefa...")
        else:
            await event.reply("🧠 Consultando múltiplas IAs...")
        resposta = await perguntar_multi_agente(texto)

        # Detecta se tem código e salva arquivo
        import re as _re, time as _time
        padrao = _re.compile(r"CODEBLOCK(\w*)\n(.*?)CODEBLOCK".replace("CODEBLOCK", "```"), _re.DOTALL)
        blocos = padrao.findall(resposta)
        if blocos:
            for lang, codigo in blocos:
                lang = lang.lower().strip()
                ext_map = {"html": "html", "python": "py", "py": "py",
                           "javascript": "js", "js": "js", "css": "css",
                           "bash": "sh", "sh": "sh"}
                ext = ext_map.get(lang, "txt")
                nome = f"jottaia_{int(_time.time())}.{ext}"
                caminho = f"/sdcard/Download/{nome}"
                try:
                    with open(caminho, "w") as f:
                        f.write(codigo)
                    await client.send_file(USUARIO_AUTORIZADO, caminho,
                        caption=f"💾 {nome} — abra no navegador!")
                except Exception:
                    pass

        await event.reply(resposta)

    else:
        # Tenta shell, senão resposta normal
        cmd = await ia_para_shell(texto)
        if cmd and cmd.strip() != "NAO_E_COMANDO" and "\n" not in cmd.strip() and len(cmd.strip()) < 300:
            cmd = cmd.strip().strip("`").strip()
            await event.reply(f"⚙️ `{cmd}`")
            saida = await executar_shell(cmd)
            await event.reply(f"```\n{saida}\n```")
        else:
            resposta, _ = await perguntar_ia(texto)
            await event.reply(resposta)


async def enviar_notificacao(texto: str):
    await client.send_message(USUARIO_AUTORIZADO, texto)


async def main():
    await client.start()
    me = await client.get_me()
    print(f"[Jotta.ia v6.2] Conectado como @{me.username}")
    print(f"[Jotta.ia v6.2] Provedor: {PROVEDORES[provedor_atual][0]}")
    print(f"[Jotta.ia v6.2] Config: {CONFIG_FILE}")
    print("[Jotta.ia v6.2] Aguardando mensagens...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
