import os
import re
from flask import Flask, redirect, render_template_string, request, session, url_for, send_file, Response
from google.oauth2.service_account import Credentials
import gspread
from datetime import datetime, date
from werkzeug.utils import secure_filename
import urllib.request
import json
import base64
import urllib.parse

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chave_secreta_crm")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ESCOPOS = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

@app.errorhandler(413)
def arquivo_muito_grande(e):
    return Response("Arquivo muito grande. O limite para anexos é de 12 MB.", status=413)

def conectar_planilha():
    credenciais_json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    
    if credenciais_json_str:
        cred_dict = json.loads(credenciais_json_str)
        credenciais = Credentials.from_service_account_info(cred_dict, scopes=ESCOPOS)
    else:
        if not os.path.exists("credenciais.json") or os.path.getsize("credenciais.json") == 0:
            raise Exception("O arquivo credenciais.json está vazio ou não existe, e a variável de ambiente não foi configurada.")
        credenciais = Credentials.from_service_account_file("credenciais.json", scopes=ESCOPOS)
        
    cliente = gspread.authorize(credenciais)
    return cliente.open_by_key("1ipTMuRsJYA_yr0dWXgh1kBAgQppSNTOUm78dUJim8LE")

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

def validar_upload_imagem(arquivo):
    if not arquivo or not arquivo.filename:
        return False, "Nenhum arquivo selecionado."
    nome = secure_filename(arquivo.filename)
    ext = os.path.splitext(nome)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return False, "Formato inválido. Use JPG, JPEG, PNG ou WEBP."
    return True, nome

def nome_seguro_anexo(contrato, quantidade, modelo, cliente, sufixo, extensao):
    base = f"{contrato}_{quantidade}_{modelo}_{cliente}_{sufixo}"
    base = secure_filename(base) or "comprovante"
    ext = extensao.lower() if extensao.lower() in ALLOWED_IMAGE_EXTENSIONS else ".jpg"
    return f"{base}{ext}"

def salvar_arquivo_google_drive(caminho_local, nome_arquivo):
    try:
        url_script = "https://script.google.com/macros/s/AKfycbw2Mk6t4XinrziGHn84Y3dNWgsPLCAUFCOpyq_EcvCUFtZbGtI-_DBP_PC2c7RBlqEPwg/exec"
        mimetype = 'image/png'
        if nome_arquivo.lower().endswith(('.jpg', '.jpeg')):
            mimetype = 'image/jpeg'
        elif nome_arquivo.lower().endswith('.pdf'):
            mimetype = 'application/pdf'
            
        with open(caminho_local, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
            
        data = {"fileName": nome_arquivo, "mimeType": mimetype, "fileData": encoded_string}
        req = urllib.request.Request(
            url_script, 
            data=json.dumps(data).encode('utf-8'), 
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req) as response:
            resposta = response.read().decode('utf-8').strip()
            
        if "Erro" not in resposta and "http" in resposta:
            return resposta
        else:
            return f"/{caminho_local}"
    except Exception as e:
        return f"/{caminho_local}"

def extrair_id_google_drive(url):
    if not url:
        return None
    texto = str(url).strip()
    padroes = [r'/file/d/([a-zA-Z0-9_-]+)', r'[?&]id=([a-zA-Z0-9_-]+)', r'/d/([a-zA-Z0-9_-]+)']
    for padrao in padroes:
        m = re.search(padrao, texto)
        if m:
            return m.group(1)
    return None

def obter_url_imagem_impressao(url, linha_id=None, tipo=None):
    if not url:
        return ''
    texto = str(url).strip()
    if texto.startswith('/'):
        return texto
    drive_id = extrair_id_google_drive(texto)
    if drive_id:
        if linha_id and tipo:
            return url_for('anexo_impressao', linha_id=linha_id, tipo=tipo)
        return f'https://drive.google.com/uc?export=download&id={drive_id}'
    return texto

def _buscar_anexo_venda(linha_id, tipo):
    planilha = conectar_planilha()
    aba = planilha.worksheet('Vendas_PM')
    linha = aba.row_values(int(linha_id))
    while len(linha) < 13:
        linha.append('')
    return str(linha[11]).strip()

@app.route('/vendas_pm/anexo/<int:linha_id>/<tipo>')
def anexo_impressao(linha_id, tipo):
    if not session.get('logado'):
        return redirect(url_for('login'))
    try:
        url_anexo = _buscar_anexo_venda(linha_id, tipo)
        if not url_anexo:
            return Response('Anexo não encontrado.', status=404)
        if url_anexo.startswith('/'):
            caminho_relativo = url_anexo.lstrip('/')
            caminho_local = os.path.abspath(caminho_relativo)
            if os.path.isfile(caminho_local):
                return send_file(caminho_local, conditional=True)
        drive_id = extrair_id_google_drive(url_anexo)
        urls_tentativas = []
        if drive_id:
            urls_tentativas.extend([
                f'https://drive.google.com/uc?export=download&id={drive_id}',
                f'https://drive.google.com/thumbnail?id={drive_id}&sz=w2000'
            ])
        else:
            urls_tentativas.append(url_anexo)
        ultimo_erro = None
        for url in urls_tentativas:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=20) as resposta:
                    conteudo = resposta.read()
                    content_type = resposta.headers.get_content_type() or 'image/jpeg'
                    if content_type == 'text/html':
                        continue
                    return Response(conteudo, status=200, mimetype=content_type, headers={'Cache-Control': 'private, max-age=3600'})
            except Exception as e:
                ultimo_erro = e
                continue
        return Response(f'Não foi possível carregar o comprovante: {ultimo_erro}', status=404)
    except Exception as e:
        return Response(f'Erro ao carregar comprovante: {e}', status=500)

def calcular_comissao_vendedor(modelo, plano, rio, planilha):
    mod_upper = str(modelo).strip().upper()
    comb_prod = f"{str(plano)} {str(rio)}".upper()
    comissao_modelo = 250
    try:
        aba_modelos = planilha.worksheet("Modelos")
        dados_modelos = aba_modelos.get_all_values()
        if len(dados_modelos) > 1:
            cabecalhos = [str(h).strip().upper() for h in dados_modelos[0]]
            idx_mod = cabecalhos.index("MODELO") if "MODELO" in cabecalhos else 3
            idx_cat = cabecalhos.index("CATEGORIA") if "CATEGORIA" in cabecalhos else 2
            categoria_encontrada = ""
            for linha in dados_modelos[1:]:
                if len(linha) > max(idx_mod, idx_cat):
                    if linha[idx_mod].strip().upper() == mod_upper:
                        categoria_encontrada = linha[idx_cat].strip().upper()
                        break
            if "DELIVERY" in categoria_encontrada:
                comissao_modelo = 200
            elif "CONSTELLATION" in categoria_encontrada:
                comissao_modelo = 300
            elif "METEOR" in categoria_encontrada:
                comissao_modelo = 500
    except Exception:
        if "DELIVERY" in mod_upper:
            comissao_modelo = 200
        elif "CONSTELLATION" in mod_upper:
            comissao_modelo = 300
        elif "METEOR" in mod_upper:
            comissao_modelo = 500

    comissao_rio = 200 if "RIO" in comb_prod or "DIAGNÓSTICO" in comb_prod else 0
    return comissao_modelo, comissao_rio, (comissao_modelo + comissao_rio)

def calcular_comissao_apm(plano, rio):
    comb_prod = f"{str(plano)} {str(rio)}".upper()
    comissao_base = 250
    comissao_rio = 150 if "RIO" in comb_prod or "DIAGNÓSTICO" in comb_prod else 0
    return comissao_base, comissao_rio, (comissao_base + comissao_rio)

def parse_data_flexivel(valor):
    if not valor:
        return None
    texto = str(valor).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    return None

def inteiro_seguro(valor, padrao=1):
    try:
        return max(0, int(float(str(valor).strip().replace(',', '.'))))
    except (ValueError, TypeError):
        return padrao

def classificar_status_contrato(status, data_final, hoje=None):
    hoje = hoje or date.today()
    texto = str(status or '').strip().upper()
    if 'CANCEL' in texto:
        return 'Cancelado'
    final = parse_data_flexivel(data_final)
    if final:
        dias = (final - hoje).days
        if dias < 0:
            return 'Vencido'
        if dias <= 15:
            return 'Vencendo'
    if 'PEND' in texto:
        return 'Pendente'
    return 'Ativo'

def montar_dados_dashboard(planilha, ano, periodo="todos"):
    hoje = date.today()
    dados_v = planilha.worksheet('Vendas_PM').get_all_values()
    k = {'contratos':0,'veiculos':0,'rio':0,'comissao_vendedor':0.0,'comissao_apm':0.0,
         'ativos':0,'pendentes':0,'vencendo':0,'vencidos':0,'cancelados':0,'sem_comprovante':0}
    vendedores, regioes = {}, {}
    meses = {i:{'contratos':0,'veiculos':0,'comissao':0.0} for i in range(1,13)}
    ultimos=[]
    
    for idx, linha in enumerate(dados_v[1:], start=2):
        while len(linha)<13: linha.append('')
        dt = parse_data_flexivel(linha[3])
        if not dt or str(dt.year) != str(ano): 
            continue
        
        if periodo != "todos":
            if periodo == "s1" and not (1 <= dt.month <= 6): continue
            elif periodo == "s2" and not (7 <= dt.month <= 12): continue
            elif periodo.isdigit() and dt.month != int(periodo): continue

        cliente, plano, rio, modelo = linha[0], linha[1], linha[2], linha[4]
        qtd = inteiro_seguro(linha[5], 1)
        vendedor, contrato, status = linha[6], linha[7], linha[8]
        
        unit_modelo, unit_rio, _ = calcular_comissao_vendedor(modelo, plano, rio, planilha)
        comissao = (unit_modelo + unit_rio) * qtd
        _, _, apm_unit = calcular_comissao_apm(plano, rio)
        
        status_calc = classificar_status_contrato(status, linha[10], hoje)
        k['contratos'] += 1
        k['veiculos'] += qtd
        k['comissao_vendedor'] += comissao
        k['comissao_apm'] += apm_unit * qtd
        if str(rio).strip() and str(rio).strip().lower() != 'nenhum': 
            k['rio'] += qtd
        if not linha[11]: 
            k['sem_comprovante'] += 1
            
        k[{'Ativo':'ativos','Pendente':'pendentes','Vencendo':'vencendo','Vencido':'vencidos','Cancelado':'cancelados'}[status_calc]] += 1
        
        vn = vendedor or 'Sem vendedor'
        vendedores.setdefault(vn, {'contratos':0, 'veiculos':0, 'comissao':0.0})
        vendedores[vn]['contratos'] += 1
        vendedores[vn]['veiculos'] += qtd
        vendedores[vn]['comissao'] += comissao
        
        reg = 'ALAGOAS (AL)' if any(x in str(vendedor).upper() for x in ('ALAGOAS','NARUHITO','KLEBER')) else 'PERNAMBUCO (PE)'
        regioes.setdefault(reg, {'contratos':0, 'veiculos':0, 'comissao':0.0})
        regioes[reg]['contratos'] += 1
        regioes[reg]['veiculos'] += qtd
        regioes[reg]['comissao'] += comissao
        
        meses[dt.month]['contratos'] += 1
        meses[dt.month]['veiculos'] += qtd
        meses[dt.month]['comissao'] += comissao
        
        ultimos.append({
            'cliente': cliente, 'contrato': contrato, 'vendedor': vendedor, 
            'modelo': modelo, 'qtd': qtd, 'status': status_calc, 
            'data': dt.strftime('%d/%m/%Y'), 'comissao': comissao, 'id_linha': idx
        })
    
    ultimos.sort(key=lambda x: x['id_linha'], reverse=True)

    dados_n = planilha.worksheet('Negocios_PM').get_all_values()
    neg_kpi = {'total': 0, 'fechado': 0, 'super_quente': 0, 'quente': 0, 'morno': 0, 'frio': 0, 'perdida': 0}
    frios_lista = []
    
    for idx, linha in enumerate(dados_n[1:], start=2):
        while len(linha) < 10: linha.append('')
        dt_n = parse_data_flexivel(linha[1])
        if not dt_n or str(dt_n.year) != str(ano):
            continue
            
        if periodo != "todos":
            if periodo == "s1" and not (1 <= dt_n.month <= 6): continue
            elif periodo == "s2" and not (7 <= dt_n.month <= 12): continue
            elif periodo.isdigit() and dt_n.month != int(periodo): continue

        temp = str(linha[0]).strip().title()
        neg_kpi['total'] += 1
        
        if temp == 'Fechado': neg_kpi['fechado'] += 1
        elif temp == 'Super Quente': neg_kpi['super_quente'] += 1
        elif temp == 'Quente': neg_kpi['quente'] += 1
        elif temp == 'Morno': neg_kpi['morno'] += 1
        elif temp == 'Perdida': neg_kpi['perdida'] += 1
        elif temp == 'Frio': 
            neg_kpi['frio'] += 1
            frios_lista.append({
                'id_linha': idx,
                'data': dt_n.strftime('%d/%m/%Y'),
                'vendedor': linha[2],
                'cliente': linha[3],
                'modelo': linha[4],
                'contato': linha[7],
                'telefone': linha[8],
                'comentarios': linha[9]
            })

    return k, vendedores, regioes, meses, ultimos[:8], neg_kpi, frios_lista


# ESTILOS COMUNS DE RESPONSIVIDADE (MOBILE & DESKTOP)
MOBILE_CSS = """
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }
body { margin: 0; font-family: Arial, sans-serif; background: #f0f4f8; color: #1a202c; display: flex; height: 100vh; overflow: hidden; }
.sidebar { width: 260px; flex-shrink: 0; background: #0D3B66; color: #fff; display: flex; flex-direction: column; transition: transform 0.3s ease; z-index: 1000; height: 100vh; position: relative; }
.sidebar-header { padding: 20px; text-align: center; border-bottom: 1px solid rgba(255,255,255,.1); }
.sidebar-header img { max-width: 150px; display: block; margin: 0 auto; }
.user-profile { padding: 15px 20px; display: flex; gap: 12px; align-items: center; border-bottom: 1px solid rgba(255,255,255,.1); }
.user-avatar { width: 36px; height: 36px; background: #fff; color: #0D3B66; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; flex-shrink: 0; }
.user-details { overflow: hidden; }
.user-name { font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #fff; font-size: 13px; }
.user-cargo { font-size: 10px; color: #90cdf4; text-transform: uppercase; }
.sidebar-menu { list-style: none; padding: 0; margin: 0; overflow-y: auto; flex: 1; }
.sidebar-menu li { border-bottom: 1px solid rgba(255,255,255,.05); }
.sidebar-menu a { display: block; padding: 12px 20px; color: #fff; text-decoration: none; font-size: 13px; }
.sidebar-menu > li > a { color: #90cdf4; font-weight: bold; text-transform: uppercase; }
.submenu { list-style: none; padding: 0; margin: 0; }
.submenu a { padding-left: 30px; font-size: 12px; color: #fff; }
.submenu a:hover { background: #134e85; }

.main-content { flex: 1; height: 100vh; overflow-y: auto; display: flex; flex-direction: column; }
.navbar { position: sticky; top: 0; z-index: 10; background: #0D3B66; color: #fff; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 6px rgba(0,0,0,.15); }
.content-body { padding: 20px; max-width: 1500px; margin: auto; width: 100%; flex: 1; }

.menu-toggle { display: none; background: none; border: none; color: white; font-size: 24px; cursor: pointer; padding: 0; margin-right: 15px; }

@media (max-width: 900px) {
    body { flex-direction: column; height: auto; overflow: auto; }
    .sidebar { position: fixed; top: 0; left: 0; height: 100%; transform: translateX(-100%); width: 260px; }
    .sidebar.ativo { transform: translateX(0); }
    .menu-toggle { display: inline-block; }
    .main-content { height: auto; overflow: visible; width: 100%; }
}
</style>
<script>
function toggleMenu() {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) sidebar.classList.toggle('ativo');
}
</script>
"""

def gerar_sidebar():
    return render_template_string("""
    <div class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <a href="/dashboard" title="Ir para o Início">
                <img src="{{ url_for('static', filename='logonovomundoazultransp.png') }}" alt="Logo Novo Mundo">
            </a>
        </div>
        <div class="user-profile">
            <div class="user-avatar">{{ session.get('nome', 'U')[0].upper() }}</div>
            <div class="user-details">
                <div class="user-name">{{ session.get('nome') }}</div>
                <div class="user-cargo">{{ session.get('perfil') }}</div>
            </div>
        </div>
        <ul class="sidebar-menu">
            {% if permissoes.pm_negocios or permissoes.pm_vendas or permissoes.pm_gerenciador %}
            <li>
                <a>📦 Plano de Manutenção </a>
                <ul class="submenu">
                    {% if permissoes.pm_negocios %}<li><a href="/negocios_pm">• Negócios em Andamento</a></li>{% endif %}
                    {% if permissoes.pm_vendas %}<li><a href="/vendas_pm">• Vendas Confirmadas</a></li>{% endif %}
                    {% if permissoes.pm_gerenciador %}<li><a href="/gerenciador_pm">• Gerenciador de Vendas</a></li>{% endif %}
                    <li><a href="/dashboard">• Dashboard</a></li>
                </ul>
            </li>
            {% endif %}
            {% if permissoes.loc_negocios or permissoes.loc_vendas or permissoes.loc_gerenciador %}
            <li>
                <a>🚚 Locação </a>
                <ul class="submenu">
                    {% if permissoes.loc_negocios %}<li><a href="/negocios_loc">• Negócios em Andamento</a></li>{% endif %}
                    {% if permissoes.loc_vendas %}<li><a href="#">• Vendas Confirmadas</a></li>{% endif %}
                    {% if permissoes.loc_gerenciador %}<li><a href="#">• Gerenciador de Vendas</a></li>{% endif %}
                </ul>
            </li>
            {% endif %}
            {% if permissoes.cons_negocios or permissoes.cons_vendas or permissoes.cons_gerenciador %}
            <li>
                <a>🤝 Consórcio </a>
                <ul class="submenu">
                    {% if permissoes.cons_negocios %}<li><a href="/negocios_cons">• Negócios em Andamento</a></li>{% endif %}
                    {% if permissoes.cons_vendas %}<li><a href="#">• Vendas Confirmadas</a></li>{% endif %}
                    {% if permissoes.cons_gerenciador %}<li><a href="#">• Gerenciador de Vendas</a></li>{% endif %}
                </ul>
            </li>
            {% endif %}
            <li><a href="/logout" style="color: #feb2b2;">🚪 Sair da Conta</a></li>
        </ul>
    </div>
    """, permissoes=session.get("permissoes", {}))


TEMPLATE_LOGIN = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Acesso Restrito - CRM Novo Mundo</title>
    <style>
        body { background: #ffffff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; padding: 15px; }
        .card-login { background: #0D3B66; padding: 30px; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); width: 100%; max-width: 380px; text-align: center; color: #ffffff; }
        .logo { max-width: 200px; display: inline-block; margin-bottom: 15px; }
        .input-group { text-align: left; margin-bottom: 15px; }
        label { font-weight: bold; font-size: 12px; color: #ffffff; }
        input { width: 100%; padding: 12px; margin-top: 5px; border: 1px solid #cbd5e0; border-radius: 6px; box-sizing: border-box; font-size: 14px; background: #ffffff; color: #1a202c; }
        .btn { background: #ffffff; color: #0D3B66; padding: 12px; width: 100%; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 15px; }
        .btn:hover { background: #f7fafc; }
        .error { background: #fff5f5; color: #c53030; padding: 10px; border-radius: 6px; margin-bottom: 15px; font-size: 13px; text-align: left; }
    </style>
</head>
<body>
    <div class="card-login">
        <img src="{{ url_for('static', filename='logonovomundoazultransp.png') }}" class="logo" alt="Logo Novo Mundo">
        <h3 style="color: #ffffff; margin-top: 0; margin-bottom: 20px;">Acesso Restrito</h3>
        {% if erro %}<div class="error">{{ erro }}</div>{% endif %}
        <form method="POST">
            <div class="input-group">
                <label>E-mail Corporativo</label>
                <input type="email" name="email" required autofocus>
            </div>
            <div class="input-group">
                <label>Senha</label>
                <input type="password" name="senha" required>
            </div>
            <button type="submit" class="btn">Entrar</button>
        </form>
    </div>
</body>
</html>
"""

TEMPLATE_DASHBOARD = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Dashboard Analítico — Pós-Venda</title>
    """ + MOBILE_CSS + """
    <style>
        .toolbar { display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap; margin-bottom: 18px; }
        .toolbar h1 { margin: 0; color: #0D3B66; font-size: 20px; }
        .toolbar-actions { display: flex; gap: 8px; align-items: center; }
        .select, .btn { border: 1px solid #cbd5e0; border-radius: 6px; padding: 9px 12px; background: #fff; font-weight: bold; color: #2d3748; font-size: 13px; }
        .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 18px; }
        .kpi { background: #fff; border-radius: 10px; padding: 16px; box-shadow: 0 2px 7px rgba(0,0,0,.06); border-left: 5px solid #0D3B66; }
        .kpi-title { font-size: 10px; color: #718096; font-weight: bold; text-transform: uppercase; }
        .kpi-value { font-size: 20px; font-weight: bold; color: #0D3B66; margin-top: 5px; }
        .kpi-sub { font-size: 10px; color: #718096; margin-top: 4px; }
        .grid { display: grid; grid-template-columns: 1.25fr .75fr; gap: 16px; }
        .card { background: #fff; border-radius: 10px; padding: 18px; box-shadow: 0 2px 7px rgba(0,0,0,.06); margin-bottom: 16px; }
        .card h3 { margin: 0 0 14px; color: #0D3B66; font-size: 15px; }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(80px, 1fr)); gap: 8px; }
        .status { padding: 12px; border-radius: 7px; background: #f7fafc; text-align: center; }
        .status b { display: block; font-size: 18px; color: #0D3B66; }
        .status span { font-size: 9px; color: #718096; font-weight: bold; }
        .bar-row { display: grid; grid-template-columns: 130px 1fr 70px; gap: 8px; align-items: center; margin: 10px 0; font-size: 11px; }
        .bar { height: 10px; background: #e2e8f0; border-radius: 8px; overflow: hidden; }
        .bar > i { display: block; height: 100%; background: #0D3B66; border-radius: 8px; }
        .bar-value { text-align: right; font-weight: bold; }
        .month-grid { display: grid; grid-template-columns: repeat(12, minmax(22px, 1fr)); gap: 4px; align-items: end; height: 170px; border-bottom: 1px solid #cbd5e0; padding-top: 10px; overflow-x: auto; }
        .month { height: 100%; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 5px; }
        .month i { display: block; width: 100%; min-height: 2px; background: #0D3B66; border-radius: 4px 4px 0 0; }
        .month span { font-size: 9px; color: #718096; }
        .month strong { font-size: 7px; color: #2d3748; writing-mode: vertical-lr; }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 11px; min-width: 400px; }
        th, td { padding: 8px 7px; border-bottom: 1px solid #e2e8f0; text-align: left; }
        th { color: #718096; text-transform: uppercase; font-size: 9px; }
        .badge { display: inline-block; padding: 3px 6px; border-radius: 5px; font-weight: bold; font-size: 9px; }
        .badge-Ativo { background: #c6f6d5; color: #22543d; }
        .badge-Pendente { background: #feebc8; color: #744210; }
        .badge-Vencendo { background: #fed7d7; color: #742a2a; }
        .badge-Vencido { background: #fed7d7; color: #9b2c2c; }
        .badge-Cancelado { background: #e2e8f0; color: #4a5568; }
        .alert { padding: 10px 12px; border-radius: 6px; background: #fff5f5; color: #9b2c2c; font-size: 11px; margin-bottom: 16px; }
        .muted { color: #718096; font-size: 11px; }
        .nav-topo-abas { background: #ffffff; padding: 12px 20px; border-bottom: 1px solid #e2e8f0; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .nav-topo-titulo { font-size: 12px; font-weight: bold; color: #718096; text-transform: uppercase; margin-bottom: 8px; }
        .nav-topo-botoes { display: flex; gap: 8px; flex-wrap: wrap; }
        .nav-aba-btn { background: #edf2f7; color: #4a5568; padding: 7px 14px; border-radius: 6px; font-size: 12px; font-weight: bold; text-decoration: none; border: 1px solid #cbd5e0; }
        .nav-aba-btn.ativo { background: #0D3B66; color: #ffffff; border-color: #0D3B66; }
        @media(max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
        @media print { .nao-imprimir { display: none !important; } }
    </style>
</head>
<body>
    {{ sidebar_html | safe }}
    <div class="main-content">
        <div class="navbar">
            <div style="display: flex; align-items: center;">
                <button class="menu-toggle" onclick="toggleMenu()">☰</button>
                <b style="font-size: 15px;">📊 Dashboard Avançado</b>
            </div>
        </div>
        <div class="content-body">
            <div class="nav-topo-abas nao-imprimir">
                <div class="nav-topo-titulo">PLANO DE MANUTENÇÃO — NAVEGAÇÃO RÁPIDA</div>
                <div class="nav-topo-botoes">
                    <a href="/dashboard" class="nav-aba-btn ativo">Dashboard</a>
                    <a href="/negocios_pm" class="nav-aba-btn">Negócios em Andamento</a>
                    <a href="/vendas_pm" class="nav-aba-btn">Vendas Confirmadas</a>
                    <a href="/gerenciador_pm" class="nav-aba-btn">Gerenciador de Vendas</a>
                </div>
            </div>
            <div class="toolbar">
                <div>
                    <h1>Painel Gerencial Consolidado</h1>
                    <div class="muted">Análise de prospecções, contratos e vigências para o ano de {{ano}}</div>
                </div>
                <div class="toolbar-actions nao-imprimir">
                    <form method="GET" style="display:flex; gap:8px; align-items:center;">
                        <select name="ano" class="select" onchange="this.form.submit()">{% for a in anos_disponiveis %}<option value="{{a}}" {% if ano==a %}selected{% endif %}>{{a}}</option>{% endfor %}</select>
                        <select name="periodo" class="select" onchange="this.form.submit()">
                            <option value="todos" {% if periodo == 'todos' %}selected{% endif %}>Ano Inteiro</option>
                            <option value="s1" {% if periodo == 's1' %}selected{% endif %}>1º Semestre</option>
                            <option value="s2" {% if periodo == 's2' %}selected{% endif %}>2º Semestre</option>
                            <option value="1" {% if periodo == '1' %}selected{% endif %}>Janeiro</option>
                            <option value="2" {% if periodo == '2' %}selected{% endif %}>Fevereiro</option>
                            <option value="3" {% if periodo == '3' %}selected{% endif %}>Março</option>
                            <option value="4" {% if periodo == '4' %}selected{% endif %}>Abril</option>
                            <option value="5" {% if periodo == '5' %}selected{% endif %}>Maio</option>
                            <option value="6" {% if periodo == '6' %}selected{% endif %}>Junho</option>
                            <option value="7" {% if periodo == '7' %}selected{% endif %}>Julho</option>
                            <option value="8" {% if periodo == '8' %}selected{% endif %}>Agosto</option>
                            <option value="9" {% if periodo == '9' %}selected{% endif %}>Setembro</option>
                            <option value="10" {% if periodo == '10' %}selected{% endif %}>Outubro</option>
                            <option value="11" {% if periodo == '11' %}selected{% endif %}>Novembro</option>
                            <option value="12" {% if periodo == '12' %}selected{% endif %}>Dezembro</option>
                        </select>
                    </form>
                </div>
            </div>

            {% if kpis.sem_comprovante %}<div class="alert nao-imprimir">⚠️ Existem <b>{{kpis.sem_comprovante}}</b> contrato(s) sem comprovante anexado. Revise antes do fechamento.</div>{% endif %}

            <div class="kpis">
                <div class="kpi"><div class="kpi-title">Contratos Fechados</div><div class="kpi-value">{{kpis.contratos}}</div><div class="kpi-sub">unidades faturadas</div></div>
                <div class="kpi" style="border-left-color: #2b6cb0;"><div class="kpi-title">Frota Protegida</div><div class="kpi-value" style="color:#2b6cb0;">{{kpis.veiculos}}</div><div class="kpi-sub">veículos totais</div></div>
                <div class="kpi" style="border-left-color: #319795;"><div class="kpi-title">Telemetria RIO</div><div class="kpi-value" style="color:#319795;">{{kpis.rio}}</div><div class="kpi-sub">unidades ativas</div></div>
                <div class="kpi" style="border-left-color: #dd6b20;"><div class="kpi-title">Vencendo (15d)</div><div class="kpi-value" style="color:#dd6b20;">{{kpis.vencendo}}</div><div class="kpi-sub">requer atenção</div></div>
                <div class="kpi" style="border-left-color: #e53e3e;"><div class="kpi-title">Planos Vencidos</div><div class="kpi-value" style="color:#e53e3e;">{{kpis.vencidos}}</div><div class="kpi-sub">expirados</div></div>
            </div>

            <div class="grid">
                <div>
                    <div class="card">
                        <h3>📅 Evolução mensal — Comissões</h3>
                        <div class="month-grid">
                            {% set maior=meses.values()|map(attribute='comissao')|max %}
                            {% for m in range(1,13) %}
                            {% set valor=meses[m].comissao %}
                            <div class="month"><strong>R$ {{"%.0f"|format(valor)}}</strong><i style="height:{{(valor/maior*130) if maior else 2}}px"></i><span>{{meses_nomes[m][:3]}}</span></div>
                            {% endfor %}
                        </div>
                    </div>
                    <div class="card">
                        <h3>🏆 Desempenho por Vendedor</h3>
                        {% if vendedores %}{% set maiorv=(vendedores_ranking[0][1].comissao if vendedores_ranking else 0) %}
                        {% for nome,d in vendedores_ranking %}
                        <div class="bar-row"><b>{{nome}}</b><div class="bar"><i style="width:{{(d.comissao/maiorv*100) if maiorv else 0}}%"></i></div><div class="bar-value">R$ {{"%.0f"|format(d.comissao)}}</div></div>
                        {% endfor %}{% else %}<div class="muted">Nenhuma venda encontrada no período.</div>{% endif %}
                    </div>
                    <div class="card" style="border-left: 5px solid #718096;">
                        <h3>❄️ Negócios Frios / Perdidos (Resgate)</h3>
                        <div class="table-wrap">
                            <table>
                                <thead><tr><th>Data</th><th>Vendedor</th><th>Cliente</th><th>Modelo</th><th>Contato</th><th>Comentários</th></tr></thead>
                                <tbody>
                                    {% for f in frios_lista %}
                                    <tr><td>{{f.data}}</td><td><b>{{f.vendedor}}</b></td><td>{{f.cliente}}</td><td>{{f.modelo}}</td><td>{{f.contato}}<br><span class="muted">{{f.telefone}}</span></td><td class="muted">{{f.comentarios}}</td></tr>
                                    {% else %}
                                    <tr><td colspan="6" class="muted" style="text-align:center; padding:15px;">Nenhum negócio frio registrado.</td></tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                    <div class="card">
                        <h3>🧾 Últimos Contratos</h3>
                        <div class="table-wrap">
                            <table>
                                <thead><tr><th>Data</th><th>Cliente</th><th>Contrato</th><th>Vendedor</th><th>Status</th><th>Comissão</th></tr></thead>
                                <tbody>
                                    {% for v in ultimos %}
                                    <tr><td>{{v.data}}</td><td><b>{{v.cliente}}</b></td><td>{{v.contrato}}</td><td>{{v.vendedor}}</td><td><span class="badge badge-{{v.status}}">{{v.status}}</span></td><td>R$ {{"%.2f"|format(v.comissao)}}</td></tr>
                                    {% else %}
                                    <tr><td colspan="6" class="muted">Nenhum contrato encontrado.</td></tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <div>
                    <div class="card">
                        <h3>📌 Situação Atual dos Contratos</h3>
                        <div class="status-grid">
                            <div class="status"><b>{{kpis.ativos}}</b><span>ATIVOS</span></div>
                            <div class="status"><b>{{kpis.pendentes}}</b><span>PENDENTES</span></div>
                            <div class="status" style="border: 1px solid #dd6b20;"><b>{{kpis.vencendo}}</b><span style="color:#dd6b20;">VENCENDO</span></div>
                            <div class="status" style="border: 1px solid #e53e3e;"><b>{{kpis.vencidos}}</b><span style="color:#e53e3e;">VENCIDOS</span></div>
                            <div class="status"><b>{{kpis.cancelados}}</b><span>CANCELADOS</span></div>
                        </div>
                    </div>
                    <div class="card">
                        <h3>🎯 Funil de Negócios</h3>
                        <div style="display:flex; flex-direction:column; gap:8px; font-size:12px;">
                            <div style="display:flex; justify-content:space-between; background:#ebf8ff; padding:8px 12px; border-radius:6px;"><span>✅ Fechados:</span><b>{{neg_kpi.fechado}}</b></div>
                            <div style="display:flex; justify-content:space-between; background:#fff5f5; padding:8px 12px; border-radius:6px;"><span>🔥 Super Quentes:</span><b>{{neg_kpi.super_quente}}</b></div>
                            <div style="display:flex; justify-content:space-between; background:#fffaf0; padding:8px 12px; border-radius:6px;"><span>⚡ Quentes:</span><b>{{neg_kpi.quente}}</b></div>
                            <div style="display:flex; justify-content:space-between; background:#f7fafc; padding:8px 12px; border-radius:6px;"><span>⚠️ Mornos:</span><b>{{neg_kpi.morno}}</b></div>
                            <div style="display:flex; justify-content:space-between; background:#edf2f7; padding:8px 12px; border-radius:6px;"><span>❌ Perdidas:</span><b>{{neg_kpi.perdida}}</b></div>
                            <div style="display:flex; justify-content:space-between; background:#edf2f7; padding:8px 12px; border-radius:6px;"><span>❄️ Frios:</span><b>{{neg_kpi.frio}}</b></div>
                        </div>
                    </div>
                    <div class="card">
                        <h3>📍 Vendas por Região</h3>
                        {% if regioes %}{% set maiord=(regioes_ranking[0][1].comissao if regioes_ranking else 0) %}
                        {% for nome,d in regioes_ranking %}
                        <div class="bar-row"><b>{{nome}}</b><div class="bar"><i style="width:{{(d.comissao/maiord*100) if maiord else 0}}%"></i></div><div class="bar-value">R$ {{"%.0f"|format(d.comissao)}}</div></div>
                        <div class="muted" style="margin:-5px 0 8px 130px">{{d.contratos}} contrato(s) · {{d.veiculos}} veículo(s)</div>
                        {% endfor %}{% else %}<div class="muted">Nenhuma venda encontrada.</div>{% endif %}
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

TEMPLATE_NEGOCIOS_PM = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Negócios em Andamento - PM</title>
    """ + MOBILE_CSS + """
    <style>
        .kpi-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; margin-bottom: 20px; }
        .kpi-card { background: white; padding: 12px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-left: 4px solid #0D3B66; text-decoration: none; display: block; }
        .kpi-card.ativo { background: #ebf8ff; border: 2px solid #3182ce; }
        .kpi-title { font-size: 9px; font-weight: bold; color: #718096; text-transform: uppercase; }
        .kpi-value { font-size: 16px; font-weight: bold; color: #0D3B66; margin-top: 4px; }
        .badge-fechado { background-color: #c6f6d5; color: #22543d; padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .badge-super-quente { background-color: #9b2c2c; color: #fff5f5; padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .badge-quente { background-color: #fed7d7; color: #742a2a; padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .badge-morno { background-color: #feebc8; color: #744210; padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .badge-perdida { background-color: #fed7d7; color: #822727; padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .badge-frio { background-color: #e2e8f0; color: #2d3748; padding: 3px 6px; border-radius: 4px; font-weight: bold; }
        .card { background: white; padding: 18px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 20px; }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 11px; min-width: 750px; }
        th, td { padding: 8px 6px; border: 1px solid #e2e8f0; text-align: left; vertical-align: middle; }
        th { background: #0D3B66; color: white; cursor: pointer; user-select: none; font-size: 10px; }
        .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 12px; }
        .form-group label { display: block; font-size: 11px; font-weight: bold; color: #0D3B66; margin-bottom: 4px; }
        .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 8px; border: 1px solid #cbd5e0; border-radius: 4px; box-sizing: border-box; font-size: 12px; background: #fff; }
        .btn { background: #0D3B66; color: white; padding: 9px 14px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 13px; text-decoration: none; display: inline-block; }
        .btn-danger { background: #e53e3e; padding: 4px 8px; font-size: 10px; }
        .btn-edit { background: #2b6cb0; padding: 4px 8px; font-size: 10px; color: white; border-radius: 4px; border: none; cursor: pointer; font-weight: bold; }
        .btn-fechar { background: #276749; padding: 4px 8px; font-size: 10px; color: white; border-radius: 4px; text-decoration: none; font-weight: bold; display: inline-block; }
        .nav-topo-abas { background: #ffffff; padding: 12px 20px; border-bottom: 1px solid #e2e8f0; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .nav-topo-titulo { font-size: 12px; font-weight: bold; color: #718096; text-transform: uppercase; margin-bottom: 8px; }
        .nav-topo-botoes { display: flex; gap: 8px; flex-wrap: wrap; }
        .nav-aba-btn { background: #edf2f7; color: #4a5568; padding: 7px 14px; border-radius: 6px; font-size: 12px; font-weight: bold; text-decoration: none; border: 1px solid #cbd5e0; }
        .nav-aba-btn.ativo { background: #0D3B66; color: #ffffff; border-color: #0D3B66; }
        .bloco-impressao { display: none; }
        @media print {
            .nao-imprimir, .sidebar, .navbar, .nav-topo-abas, .kpi-container, .card-registro { display: none !important; }
            body, .main-content { background: white !important; height: auto !important; overflow: visible !important; }
            .bloco-impressao { display: block !important; }
        }
    </style>
    <script>
        function aplicarMascaras(input, tipo) {
            let v = input.value.replace(/\\D/g, "");
            if (tipo === 'tel') {
                if (v.length > 2) v = '(' + v.substring(0, 2) + ') ' + v.substring(2);
                if (v.length > 10) v = v.substring(0, 10) + '-' + v.substring(10, 14);
            }
            input.value = v;
        }
        function filtrarTabelaRapida() {
            let termo = document.getElementById("inputBuscaRapida").value.toLowerCase();
            let linhas = document.querySelectorAll("#tabelaNegocios tbody tr.tr-normal");
            linhas.forEach(linha => {
                let textoLinha = linha.innerText.toLowerCase();
                linha.style.display = textoLinha.includes(termo) ? "" : "none";
            });
        }
        function abrirEdicaoInline(linhaId) {
            document.querySelectorAll('.tr-edicao').forEach(el => el.style.display = 'none');
            document.querySelectorAll('.tr-normal').forEach(el => el.style.display = '');
            document.getElementById('normal-' + linhaId).style.display = 'none';
            document.getElementById('edit-' + linhaId).style.display = '';
        }
        function cancelarEdicaoInline(linhaId) {
            document.getElementById('edit-' + linhaId).style.display = 'none';
            document.getElementById('normal-' + linhaId).style.display = '';
        }
    </script>
</head>
<body>
    {{ sidebar_html | safe }}
    <div class="main-content">
        <div class="navbar">
            <div style="display: flex; align-items: center;">
                <button class="menu-toggle" onclick="toggleMenu()">☰</button>
                <b style="font-size: 15px;">Negócios em Andamento</b>
            </div>
        </div>
        <div class="content-body">
            <div class="nav-topo-abas nao-imprimir">
                <div class="nav-topo-titulo">PLANO DE MANUTENÇÃO — NAVEGAÇÃO RÁPIDA</div>
                <div class="nav-topo-botoes">
                    <a href="/dashboard" class="nav-aba-btn">Dashboard</a>
                    <a href="/negocios_pm" class="nav-aba-btn ativo">Negócios em Andamento</a>
                    <a href="/vendas_pm" class="nav-aba-btn">Vendas Confirmadas</a>
                    <a href="/gerenciador_pm" class="nav-aba-btn">Gerenciador de Vendas</a>
                </div>
            </div>

            {% if mensagem %}<div style="background:#c6f6d5; color:#22543d; padding:10px; border-radius:6px; margin-bottom:15px; font-weight:bold; font-size:12px;">{{ mensagem }}</div>{% endif %}

            <div class="kpi-container nao-imprimir">
                <a href="{{ url_for('negocios_pm', filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, filtro_temp='todos', filtro_vendedor=filtro_vendedor) }}" class="kpi-card {% if filtro_temp == 'todos' %}ativo{% endif %}">
                    <div class="kpi-title">Total</div><div class="kpi-value">{{ total_geral }}</div>
                </a>
                <a href="{{ url_for('negocios_pm', filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, filtro_temp='Fechado', filtro_vendedor=filtro_vendedor) }}" class="kpi-card {% if filtro_temp == 'Fechado' %}ativo{% endif %}" style="border-left-color: #276749;">
                    <div class="kpi-title">Fechados</div><div class="kpi-value" style="color: #276749;">{{ total_fechados }}</div>
                </a>
                <a href="{{ url_for('negocios_pm', filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, filtro_temp='Super Quente', filtro_vendedor=filtro_vendedor) }}" class="kpi-card {% if filtro_temp == 'Super Quente' %}ativo{% endif %}" style="border-left-color: #9b2c2c;">
                    <div class="kpi-title">Sup. Quente</div><div class="kpi-value" style="color: #9b2c2c;">{{ total_super_quente }}</div>
                </a>
                <a href="{{ url_for('negocios_pm', filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, filtro_temp='Quente', filtro_vendedor=filtro_vendedor) }}" class="kpi-card {% if filtro_temp == 'Quente' %}ativo{% endif %}" style="border-left-color: #e53e3e;">
                    <div class="kpi-title">Quentes</div><div class="kpi-value" style="color: #e53e3e;">{{ total_quentes }}</div>
                </a>
                <a href="{{ url_for('negocios_pm', filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, filtro_temp='Morno', filtro_vendedor=filtro_vendedor) }}" class="kpi-card {% if filtro_temp == 'Morno' %}ativo{% endif %}" style="border-left-color: #d69e2e;">
                    <div class="kpi-title">Mornos</div><div class="kpi-value" style="color: #d69e2e;">{{ total_mornos }}</div>
                </a>
                <a href="{{ url_for('negocios_pm', filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, filtro_temp='Perdida', filtro_vendedor=filtro_vendedor) }}" class="kpi-card {% if filtro_temp == 'Perdida' %}ativo{% endif %}" style="border-left-color: #9b2c2c;">
                    <div class="kpi-title">Perdidas</div><div class="kpi-value" style="color: #9b2c2c;">{{ total_perdida }}</div>
                </a>
                <a href="{{ url_for('negocios_pm', filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, filtro_temp='Frio', filtro_vendedor=filtro_vendedor) }}" class="kpi-card {% if filtro_temp == 'Frio' %}ativo{% endif %}" style="border-left-color: #718096;">
                    <div class="kpi-title">Frios</div><div class="kpi-value" style="color: #4a5568;">{{ total_frios }}</div>
                </a>
            </div>

            <div class="card card-registro nao-imprimir">
                <details>
                    <summary style="cursor: pointer; font-weight: bold; color: #0D3B66; font-size: 14px;">➕ Registrar Nova Negociação</summary>
                    <form method="POST" style="margin-top: 12px;">
                        <input type="hidden" name="acao" value="adicionar">
                        <div class="form-grid">
                            <div class="form-group"><label>TEMPERATURA</label><select name="temperatura" required><option value="">Selecione...</option><option value="Fechado">Fechado</option><option value="Super Quente">Super Quente</option><option value="Quente">Quente</option><option value="Morno">Morno</option><option value="Perdida">Perdida</option><option value="Frio">Frio</option></select></div>
                            <div class="form-group"><label>DATA</label><input type="date" name="data" value="{{ data_hoje_input }}" required></div>
                            <div class="form-group"><label>VENDEDOR</label><select name="vendedor" required><option value="">Selecione...</option>{% for v in consultores_lista %}<option value="{{ v }}">{{ v }}</option>{% endfor %}</select></div>
                            <div class="form-group"><label>CLIENTE</label><input type="text" name="cliente" placeholder="Nome do Cliente" required></div>
                            <div class="form-group"><label>MODELO</label><select name="modelo"><option value="">Selecione...</option>{% for mod in modelos_lista %}<option value="{{ mod }}">{{ mod }}</option>{% endfor %}</select></div>
                            <div class="form-group"><label>PLANO</label><select name="plano"><option value="">Selecione...</option>{% for p in pm_lista %}<option value="{{ p }}">{{ p }}</option>{% endfor %}</select></div>
                            <div class="form-group"><label>RIO</label><select name="rio"><option value="">Selecione...</option>{% for r in rio_lista %}<option value="{{ r }}">{{ r }}</option>{% endfor %}</select></div>
                            <div class="form-group"><label>CONTATO</label><input type="text" name="contato" placeholder="Contato"></div>
                            <div class="form-group"><label>TELEFONE</label><input type="text" name="telefone" placeholder="(00) 00000-0000" maxlength="15" oninput="aplicarMascaras(this, 'tel')"></div>
                        </div>
                        <div class="form-group" style="margin-bottom: 12px;"><label>COMENTÁRIOS</label><textarea name="comentarios" rows="2" placeholder="Andamento..."></textarea></div>
                        <button type="submit" class="btn">Salvar Nova Negociação</button>
                    </form>
                </details>
            </div>

            <div class="card" style="padding: 12px 15px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                <h3 style="color: #0D3B66; margin: 0; font-size: 14px;">Lista de Negócios</h3>
                <div class="nao-imprimir" style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap; width: 100%;">
                    <input type="text" id="inputBuscaRapida" onkeyup="filtrarTabelaRapida()" placeholder="🔍 Buscar cliente..." style="padding: 7px 10px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 12px; flex: 1; min-width: 150px; background: #fff;">
                    <form method="GET" action="/negocios_pm" style="display: flex; gap: 6px; align-items: center; margin: 0; flex-wrap: wrap;">
                        <input type="hidden" name="filtro_temp" value="{{ filtro_temp }}">
                        <select name="filtro_vendedor" onchange="this.form.submit()" style="padding: 7px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 12px; background: #fff;">
                            <option value="todos" {% if filtro_vendedor == 'todos' %}selected{% endif %}>Todos Vendedores</option>
                            {% for v in consultores_lista %}<option value="{{ v }}" {% if filtro_vendedor == v %}selected{% endif %}>{{ v }}</option>{% endfor %}
                        </select>
                        <select name="filtro_ano" onchange="this.form.submit()" style="padding: 7px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 12px; background: #fff;">
                            {% for a in anos_disponiveis %}<option value="{{ a }}" {% if filtro_ano == a %}selected{% endif %}>{{ a }}</option>{% endfor %}
                        </select>
                        <select name="filtro_periodo" onchange="this.form.submit()" style="padding: 7px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 12px; background: #fff;">
                            <option value="todos" {% if filtro_periodo == 'todos' %}selected{% endif %}>Ano Inteiro</option>
                            <option value="s1" {% if filtro_periodo == 's1' %}selected{% endif %}>1º Sem</option>
                            <option value="s2" {% if filtro_periodo == 's2' %}selected{% endif %}>2º Sem</option>
                            {% for m in range(1,13) %}<option value="{{m}}" {% if filtro_periodo == m|string %}selected{% endif %}>Mês {{m}}</option>{% endfor %}
                        </select>
                    </form>
                </div>
            </div>

            <div class="card">
                <div class="table-wrap">
                    <table id="tabelaNegocios">
                        <thead>
                            <tr>
                                <th>Temp.</th>
                                <th>Data</th>
                                <th>Vendedor</th>
                                <th>Cliente</th>
                                <th>Modelo</th>
                                <th>Plano</th>
                                <th>RIO</th>
                                <th>Contato</th>
                                <th>Telefone</th>
                                <th>Comentários</th>
                                <th class="nao-imprimir" style="text-align: center;">Ações</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for item in dados %}
                            <tr id="normal-{{ item.id_linha }}" class="tr-normal">
                                <td>
                                    {% set temp = item.temperatura | trim | title %}
                                    {% if temp == 'Fechado' %}<span class="badge-fechado">Fechado</span>
                                    {% elif temp == 'Super Quente' %}<span class="badge-super-quente">Sup. Quente</span>
                                    {% elif temp == 'Quente' %}<span class="badge-quente">Quente</span>
                                    {% elif temp == 'Morno' %}<span class="badge-morno">Morno</span>
                                    {% elif temp == 'Perdida' %}<span class="badge-perdida">Perdida</span>
                                    {% elif temp == 'Frio' %}<span class="badge-frio">Frio</span>
                                    {% else %}{{ item.temperatura }}{% endif %}
                                </td>
                                <td>{{ item.data }}</td>
                                <td>{{ item.vendedor }}</td>
                                <td><b>{{ item.cliente }}</b></td>
                                <td>{{ item.modelo }}</td>
                                <td>{{ item.plano }}</td>
                                <td>{{ item.rio }}</td>
                                <td>{{ item.contato }}</td>
                                <td>{{ item.telefone }}</td>
                                <td style="white-space: pre-wrap; font-size: 10px;">{{ item.comentarios }}</td>
                                <td class="nao-imprimir" style="text-align: center; white-space: nowrap;">
                                    <div style="display: flex; gap: 4px; justify-content: center; align-items: center;">
                                        {% if temp == 'Fechado' %}<a href="/vendas_pm/fechar/{{ item.id_linha }}" class="btn-fechar">✔️ Venda</a>{% endif %}
                                        <button type="button" class="btn-edit" onclick="abrirEdicaoInline('{{ item.id_linha }}')">Alterar</button>
                                        <form action="/negocios_pm/excluir/{{ item.id_linha }}" method="POST" style="display:inline; margin: 0;" onsubmit="return confirm('Excluir?');">
                                            <button type="submit" class="btn btn-danger">Excluir</button>
                                        </form>
                                    </div>
                                </td>
                            </tr>
                            <tr id="edit-{{ item.id_linha }}" class="tr-edicao nao-imprimir" style="display: none; background: #f7fafc;">
                                <form method="POST">
                                    <input type="hidden" name="acao" value="editar">
                                    <input type="hidden" name="linha_id" value="{{ item.id_linha }}">
                                    <input type="hidden" name="filtro_ano" value="{{ filtro_ano }}">
                                    <input type="hidden" name="filtro_periodo" value="{{ filtro_periodo }}">
                                    <input type="hidden" name="filtro_temp" value="{{ filtro_temp }}">
                                    <input type="hidden" name="filtro_vendedor" value="{{ filtro_vendedor }}">
                                    <td><select name="temperatura" required><option value="Fechado" {% if item.temperatura == 'Fechado' %}selected{% endif %}>Fechado</option><option value="Super Quente" {% if item.temperatura == 'Super Quente' %}selected{% endif %}>Sup. Quente</option><option value="Quente" {% if item.temperatura == 'Quente' %}selected{% endif %}>Quente</option><option value="Morno" {% if item.temperatura == 'Morno' %}selected{% endif %}>Morno</option><option value="Perdida" {% if item.temperatura == 'Perdida' %}selected{% endif %}>Perdida</option><option value="Frio" {% if item.temperatura == 'Frio' %}selected{% endif %}>Frio</option></select></td>
                                    <td><input type="date" name="data" value="{{ item.data_iso }}" required></td>
                                    <td><select name="vendedor" required>{% for v in consultores_lista %}<option value="{{ v }}" {% if item.vendedor == v %}selected{% endif %}>{{ v }}</option>{% endfor %}</select></td>
                                    <td><input type="text" name="cliente" value="{{ item.cliente }}" required></td>
                                    <td><select name="modelo"><option value="">Selecione...</option>{% for mod in modelos_lista %}<option value="{{ mod }}" {% if item.modelo == mod %}selected{% endif %}>{{ mod }}</option>{% endfor %}</select></td>
                                    <td><select name="plano"><option value="">Selecione...</option>{% for p in pm_lista %}<option value="{{ p }}" {% if item.plano == p %}selected{% endif %}>{{ p }}</option>{% endfor %}</select></td>
                                    <td><select name="rio"><option value="">Selecione...</option>{% for r in rio_lista %}<option value="{{ r }}" {% if item.rio == r %}selected{% endif %}>{{ r }}</option>{% endfor %}</select></td>
                                    <td><input type="text" name="contato" value="{{ item.contato }}"></td>
                                    <td><input type="text" name="telefone" value="{{ item.telefone }}" maxlength="15" oninput="aplicarMascaras(this, 'tel')"></td>
                                    <td><textarea name="comentarios" rows="2">{{ item.comentarios }}</textarea></td>
                                    <td style="text-align: center;"><button type="submit" class="btn" style="padding: 4px; font-size: 10px; background: #276749;">Salvar</button> <button type="button" class="btn" style="padding: 4px; font-size: 10px; background: #718096;" onclick="cancelarEdicaoInline('{{ item.id_linha }}')">X</button></td>
                                </form>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

TEMPLATE_GERENCIADOR_PM = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Gerenciador de Vendas e Comissões</title>
    """ + MOBILE_CSS + """
    <style>
        .kpi-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 20px; }
        .kpi-card { background: white; padding: 12px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-left: 4px solid #0D3B66; }
        .kpi-title { font-size: 9px; font-weight: bold; color: #718096; text-transform: uppercase; }
        .kpi-value { font-size: 16px; font-weight: bold; color: #0D3B66; margin-top: 4px; }
        .card { background: white; padding: 18px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 20px; }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 11px; min-width: 600px; }
        th, td { padding: 8px 6px; border: 1px solid #e2e8f0; text-align: left; }
        th { background: #0D3B66; color: white; font-size: 10px; }
        .btn { background: #0D3B66; color: white; padding: 8px 12px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 12px; text-decoration: none; display: inline-block; }
        .regiao-box { border: 1px solid #cbd5e0; border-radius: 8px; margin-bottom: 20px; background: #fff; overflow: hidden; }
        .regiao-header { background: #0D3B66; color: white; padding: 10px 15px; font-weight: bold; font-size: 13px; text-transform: uppercase; }
        .vendedor-box { padding: 12px; border-bottom: 1px solid #e2e8f0; }
        .nav-topo-abas { background: #ffffff; padding: 12px 20px; border-bottom: 1px solid #e2e8f0; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .nav-topo-titulo { font-size: 12px; font-weight: bold; color: #718096; text-transform: uppercase; margin-bottom: 8px; }
        .nav-topo-botoes { display: flex; gap: 8px; flex-wrap: wrap; }
        .nav-aba-btn { background: #edf2f7; color: #4a5568; padding: 7px 14px; border-radius: 6px; font-size: 12px; font-weight: bold; text-decoration: none; border: 1px solid #cbd5e0; }
        .nav-aba-btn.ativo { background: #0D3B66; color: #ffffff; border-color: #0D3B66; }
    </style>
</head>
<body>
    {{ sidebar_html | safe }}
    <div class="main-content">
        <div class="navbar">
            <div style="display: flex; align-items: center;">
                <button class="menu-toggle" onclick="toggleMenu()">☰</button>
                <b style="font-size: 15px;">Gerenciador de Vendas</b>
            </div>
        </div>
        <div class="content-body">
            <div class="nav-topo-abas nao-imprimir">
                <div class="nav-topo-titulo">PLANO DE MANUTENÇÃO — NAVEGAÇÃO RÁPIDA</div>
                <div class="nav-topo-botoes">
                    <a href="/dashboard" class="nav-aba-btn">Dashboard</a>
                    <a href="/negocios_pm" class="nav-aba-btn">Negócios em Andamento</a>
                    <a href="/vendas_pm" class="nav-aba-btn">Vendas Confirmadas</a>
                    <a href="/gerenciador_pm" class="nav-aba-btn ativo">Gerenciador de Vendas</a>
                </div>
            </div>

            <div class="kpi-container nao-imprimir">
                <div class="kpi-card"><div class="kpi-title">Total Vendas</div><div class="kpi-value">{{ total_vendas_qtd }} un</div></div>
                <div class="kpi-card" style="border-left-color: #2b6cb0;"><div class="kpi-title">Total RIO</div><div class="kpi-value" style="color: #2b6cb0;">{{ total_rio_qtd }} un</div></div>
                <div class="kpi-card" style="border-left-color: #0D3B66;"><div class="kpi-title">Comissão AL</div><div class="kpi-value" style="color: #0D3B66;">R$ {{ "%.2f" | format(total_comissao_al) }}</div></div>
                <div class="kpi-card" style="border-left-color: #2b6cb0;"><div class="kpi-title">Comissão PE</div><div class="kpi-value" style="color: #2b6cb0;">R$ {{ "%.2f" | format(total_comissao_pe) }}</div></div>
            </div>

            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; flex-wrap: wrap; gap: 10px;">
                    <h3 style="color: #0D3B66; margin: 0; font-size: 15px;">📊 Apuração de Comissões</h3>
                    <form method="GET" action="/gerenciador_pm" style="display: flex; gap: 6px; align-items: center; margin: 0;">
                        <select name="filtro_ano" onchange="this.form.submit()" style="padding: 6px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 11px;">
                            {% for a in anos_disponiveis %}<option value="{{ a }}" {% if filtro_ano == a %}selected{% endif %}>{{ a }}</option>{% endfor %}
                        </select>
                        <select name="filtro_periodo" onchange="this.form.submit()" style="padding: 6px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 11px;">
                            <option value="todos" {% if filtro_periodo == 'todos' %}selected{% endif %}>Ano Inteiro</option>
                            <option value="s1" {% if filtro_periodo == 's1' %}selected{% endif %}>1º Sem</option>
                            <option value="s2" {% if filtro_periodo == 's2' %}selected{% endif %}>2º Sem</option>
                            {% for m in range(1,13) %}<option value="{{m}}" {% if filtro_periodo == m|string %}selected{% endif %}>Mês {{m}}</option>{% endfor %}
                        </select>
                    </form>
                </div>
                
                {% for regiao, dados_reg in relatorio_por_regiao.items() %}
                <div class="regiao-box">
                    <div class="regiao-header">📍 Região: {{ regiao }}</div>
                    {% for vendedor, dados_v in dados_reg.vendedores.items() %}
                    <div class="vendedor-box">
                        <div style="font-weight: bold; font-size: 12px; color: #0D3B66; margin-bottom: 6px;">Vendedor(a): {{ vendedor }}</div>
                        <div class="table-wrap">
                            <table>
                                <thead><tr><th>Cliente</th><th>Contrato</th><th>Modelo</th><th>Qtd</th><th>Cálculo</th><th>Comissão</th></tr></thead>
                                <tbody>
                                    {% for item in dados_v.itens %}
                                    <tr>
                                        <td><b>{{ item.cliente }}</b></td>
                                        <td>{{ item.contrato }}</td>
                                        <td>{{ item.modelo }}</td>
                                        <td style="text-align: center;">{{ item.quantidade }}</td>
                                        <td>Plano: R${{ "%.2f" | format(item.unit_modelo) }} | RIO: R${{ "%.2f" | format(item.unit_rio) }}</td>
                                        <td><b>R$ {{ "%.2f" | format(item.comissao_vendedor) }}</b></td>
                                    </tr>
                                    {% if item.anexo1 %}
                                    <tr><td colspan="6" style="background: #fafbfc; padding: 8px;"><a href="{{ obter_url_imagem_impressao(item.anexo1, item.id_linha, '1') }}" target="_blank" style="color: #2b6cb0; font-size: 11px;">📄 Ver Comprovante</a></td></tr>
                                    {% endif %}
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        <div style="text-align: right; font-size: 11px; color: #2d3748; margin-top: 6px;">Total {{ vendedor }}: <b>R$ {{ "%.2f" | format(dados_v.total_vendedor) }}</b></div>
                    </div>
                    {% endfor %}
                    <div style="background: #edf2f7; padding: 8px 12px; text-align: right; font-weight: bold; color: #0D3B66; font-size: 12px;">Total Região {{ regiao }}: R$ {{ "%.2f" | format(dados_reg.total_regiao) }}</div>
                </div>
                {% else %}
                <p style="text-align: center; color: #718096; padding: 15px;">Nenhuma comissão registrada.</p>
                {% endfor %}

                {% if relatorio_por_regiao %}
                <div style="background: #f0f4f8; padding: 15px; border-top: 3px solid #0D3B66; margin-top: 20px; border-radius: 8px;">
                    <div style="font-weight: bold; font-size: 13px; color: #0D3B66; margin-bottom: 10px; text-align: center;">🔵 Resumo Geral APM</div>
                    <div style="text-align: right; font-size: 15px; color: #0D3B66; font-weight: bold;">TOTAL GERAL APM: R$ {{ "%.2f" | format(resumo_apm.geral) }}</div>
                </div>
                {% endif %}
            </div>
        </div>
    </div>
</body>
</html>
"""

TEMPLATE_VENDAS_PM = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Vendas Confirmadas - PM</title>
    """ + MOBILE_CSS + """
    <style>
        .kpi-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 20px; }
        .kpi-card { background: white; padding: 12px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-left: 4px solid #0D3B66; }
        .kpi-title { font-size: 9px; font-weight: bold; color: #718096; text-transform: uppercase; }
        .kpi-value { font-size: 16px; font-weight: bold; color: #0D3B66; margin-top: 4px; }
        .card { background: white; padding: 18px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 20px; }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 11px; min-width: 800px; }
        th, td { padding: 8px 6px; border: 1px solid #e2e8f0; text-align: left; vertical-align: middle; }
        th { background: #0D3B66; color: white; font-size: 10px; }
        .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 12px; }
        .form-group label { display: block; font-size: 11px; font-weight: bold; color: #0D3B66; margin-bottom: 4px; }
        .form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid #cbd5e0; border-radius: 4px; box-sizing: border-box; font-size: 12px; background: #fff; }
        .btn { background: #0D3B66; color: white; padding: 9px 14px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 13px; text-decoration: none; display: inline-block; }
        .btn-danger { background: #e53e3e; padding: 4px 8px; font-size: 10px; }
        .btn-edit { background: #2b6cb0; padding: 4px 8px; font-size: 10px; color: white; border-radius: 4px; text-decoration: none; font-weight: bold; display: inline-block; text-align: center; }
        .btn-whatsapp { background: #25d366; color: white; padding: 3px 6px; font-size: 10px; border-radius: 4px; text-decoration: none; font-weight: bold; display: inline-block; margin-top: 3px; }
        .badge-vencimento { padding: 2px 5px; border-radius: 4px; font-weight: bold; font-size: 9px; display: inline-block; margin-top: 2px; }
        .vencido { background: #fed7d7; color: #9b2c2c; }
        .proximo { background: #feebc8; color: #744210; }
        .ok { background: #c6f6d5; color: #22543d; }
        .nav-topo-abas { background: #ffffff; padding: 12px 20px; border-bottom: 1px solid #e2e8f0; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .nav-topo-titulo { font-size: 12px; font-weight: bold; color: #718096; text-transform: uppercase; margin-bottom: 8px; }
        .nav-topo-botoes { display: flex; gap: 8px; flex-wrap: wrap; }
        .nav-aba-btn { background: #edf2f7; color: #4a5568; padding: 7px 14px; border-radius: 6px; font-size: 12px; font-weight: bold; text-decoration: none; border: 1px solid #cbd5e0; }
        .nav-aba-btn.ativo { background: #0D3B66; color: #ffffff; border-color: #0D3B66; }
    </style>
    <script>
        function filtrarVendasDigitando() {
            const campo = document.getElementById('buscaVendas');
            const termo = (campo ? campo.value : '').toLowerCase().trim();
            const filtroStatus = (document.getElementById('filtroStatusVendas')?.value || '').toLowerCase();
            const linhas = Array.from(document.querySelectorAll('#tabelaVendas tbody tr.linha-venda'));
            let visiveis = 0;
            linhas.forEach(tr => {
                const texto = tr.innerText.toLowerCase();
                const statusTexto = (tr.querySelector('td:nth-child(9)')?.innerText || '').toLowerCase().trim();
                const mostrar = (!termo || texto.includes(termo)) && (!filtroStatus || statusTexto.includes(filtroStatus));
                tr.style.display = mostrar ? '' : 'none';
                if (mostrar) visiveis++;
            });
            const contador = document.getElementById('contadorBusca');
            if (contador) contador.innerText = termo ? `${visiveis} encontrado(s)` : `${linhas.length} registro(s)`;
        }
    </script>
</head>
<body>
    {{ sidebar_html | safe }}
    <div class="main-content">
        <div class="navbar">
            <div style="display: flex; align-items: center;">
                <button class="menu-toggle" onclick="toggleMenu()">☰</button>
                <b style="font-size: 15px;">Vendas Confirmadas</b>
            </div>
        </div>
        <div class="content-body">
            <div class="nav-topo-abas nao-imprimir">
                <div class="nav-topo-titulo">PLANO DE MANUTENÇÃO — NAVEGAÇÃO RÁPIDA</div>
                <div class="nav-topo-botoes">
                    <a href="/dashboard" class="nav-aba-btn">Dashboard</a>
                    <a href="/negocios_pm" class="nav-aba-btn">Negócios em Andamento</a>
                    <a href="/vendas_pm" class="nav-aba-btn ativo">Vendas Confirmadas</a>
                    <a href="/gerenciador_pm" class="nav-aba-btn">Gerenciador de Vendas</a>
                </div>
            </div>

            {% if mensagem %}<div style="background:#c6f6d5; color:#22543d; padding:10px; border-radius:6px; margin-bottom:15px; font-weight:bold; font-size:12px;">{{ mensagem }}</div>{% endif %}

            <div class="kpi-container nao-imprimir">
                <div class="kpi-card"><div class="kpi-title">Contratos</div><div class="kpi-value">{{ total_vendas }}</div></div>
                <div class="kpi-card" style="border-left-color: #2b6cb0;"><div class="kpi-title">Veículos</div><div class="kpi-value" style="color: #2b6cb0;">{{ total_veiculos }} un</div></div>
                <div class="kpi-card" style="border-left-color: #dd6b20;"><div class="kpi-title">Vencem (15d)</div><div class="kpi-value" style="color: #dd6b20;">{{ total_proximos }}</div></div>
                <div class="kpi-card" style="border-left-color: #e53e3e;"><div class="kpi-title">Vencidos</div><div class="kpi-value" style="color: #e53e3e;">{{ total_vencidos }}</div></div>
            </div>

            <div class="card nao-imprimir" style="border-left: 5px solid #276749; background: #f8fafc;">
                {% if modo_edicao %}
                <h3 style="color: #2b6cb0; margin-top: 0; font-size: 15px;">Alterar Venda: {{ venda_editando.cliente }}</h3>
                <form method="POST" enctype="multipart/form-data">
                    <input type="hidden" name="acao" value="salvar_edicao_venda">
                    <input type="hidden" name="linha_id" value="{{ venda_editando.id_linha }}">
                {% else %}
                <h3 style="color: #276749; margin-top: 0; font-size: 15px;">Registrar Venda: {% if negocio_alvo %}{{ negocio_alvo.cliente }}{% else %}Novo Registro{% endif %}</h3>
                <form method="POST" enctype="multipart/form-data">
                    <input type="hidden" name="acao" value="salvar_venda">
                    {% if negocio_alvo %}
                    <input type="hidden" name="cliente" value="{{ negocio_alvo.cliente }}">
                    <input type="hidden" name="data_venda" value="{{ negocio_alvo.data }}">
                    <input type="hidden" name="modelo" value="{{ negocio_alvo.modelo }}">
                    <input type="hidden" name="vendedor" value="{{ negocio_alvo.vendedor }}">
                    {% endif %}
                {% endif %}

                    <div class="form-grid">
                        {% if not negocio_alvo and not modo_edicao %}
                        <div class="form-group"><label>CLIENTE</label><input type="text" name="cliente" required placeholder="Nome"></div>
                        <div class="form-group"><label>DATA DA VENDA</label><input type="date" name="data_venda" required></div>
                        <div class="form-group"><label>MODELO</label><input type="text" name="modelo" required placeholder="Modelo"></div>
                        <div class="form-group"><label>VENDEDOR</label><input type="text" name="vendedor" required placeholder="Vendedor"></div>
                        {% endif %}

                        {% if modo_edicao %}
                        <div class="form-group"><label>CLIENTE</label><input type="text" name="cliente" value="{{ venda_editando.cliente }}" required></div>
                        <div class="form-group"><label>DATA</label><input type="text" name="data_venda" value="{{ venda_editando.data_venda }}" required></div>
                        <div class="form-group"><label>MODELO</label><input type="text" name="modelo" value="{{ venda_editando.modelo }}"></div>
                        <div class="form-group"><label>VENDEDOR</label><input type="text" name="vendedor" value="{{ venda_editando.vendedor }}" required></div>
                        {% endif %}

                        <div class="form-group"><label>PLANO</label><select name="plano" required><option value="">Selecione...</option>{% for p in pm_lista %}<option value="{{ p }}" {% if negocio_alvo and negocio_alvo.plano == p %}selected{% endif %}{% if modo_edicao and venda_editando.plano == p %}selected{% endif %}>{{ p }}</option>{% endfor %}</select></div>
                        <div class="form-group"><label>RIO</label><select name="rio"><option value="">Selecione...</option>{% for r in rio_lista %}<option value="{{ r }}" {% if negocio_alvo and negocio_alvo.rio == r %}selected{% endif %}{% if modo_edicao and venda_editando.rio == r %}selected{% endif %}>{{ r }}</option>{% endfor %}</select></div>
                        <div class="form-group"><label>QTD VEÍCULOS</label><input type="number" name="quantidade" min="1" value="{% if modo_edicao %}{{ venda_editando.quantidade }}{% else %}1{% endif %}" required></div>
                        <div class="form-group"><label>CONTRATO</label><input type="text" name="contrato" placeholder="Nº Contrato" value="{% if modo_edicao %}{{ venda_editando.contrato }}{% endif %}" required></div>
                        <div class="form-group"><label>STATUS</label><select name="status" required><option value="Ativo" {% if modo_edicao and venda_editando.status == 'Ativo' %}selected{% endif %}>Ativo</option><option value="Pendente" {% if modo_edicao and venda_editando.status == 'Pendente' %}selected{% endif %}>Pendente</option><option value="Cancelado" {% if modo_edicao and venda_editando.status == 'Cancelado' %}selected{% endif %}>Cancelado</option></select></div>
                        <div class="form-group"><label>INÍCIO</label><input type="date" name="inicio" value="{% if modo_edicao %}{{ venda_editando.inicio_iso }}{% endif %}" required></div>
                        <div class="form-group"><label>FINAL</label><input type="date" name="final" value="{% if modo_edicao %}{{ venda_editando.final_iso }}{% endif %}" required></div>
                        <div class="form-group"><label>COMPROVANTE</label><input type="file" name="anexo1" accept="image/*" capture="environment"></div>
                    </div>
                    <button type="submit" class="btn" style="background: {% if modo_edicao %}#2b6cb0{% else %}#276749{% endif %};">{% if modo_edicao %}Salvar Alterações{% else %}Salvar Venda Confirmada{% endif %}</button>
                    {% if modo_edicao %}<a href="/vendas_pm" class="btn" style="background: #718096; text-decoration: none; margin-left: 5px;">Cancelar</a>{% endif %}
                </form>
            </div>

            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px;">
                    <h3 style="color: #0D3B66; margin: 0; font-size: 14px;">Contratos Registrados</h3>
                    <form method="GET" action="/vendas_pm" style="display: flex; gap: 6px; align-items: center; margin: 0;">
                        <select name="filtro_ano" onchange="this.form.submit()" style="padding: 6px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 11px;">
                            {% for a in anos_disponiveis %}<option value="{{ a }}" {% if filtro_ano == a %}selected{% endif %}>{{ a }}</option>{% endfor %}
                        </select>
                        <select name="filtro_periodo" onchange="this.form.submit()" style="padding: 6px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 11px;">
                            <option value="todos" {% if filtro_periodo == 'todos' %}selected{% endif %}>Ano Inteiro</option>
                            <option value="s1" {% if filtro_periodo == 's1' %}selected{% endif %}>1º Sem</option>
                            <option value="s2" {% if filtro_periodo == 's2' %}selected{% endif %}>2º Sem</option>
                            {% for m in range(1,13) %}<option value="{{m}}" {% if filtro_periodo == m|string %}selected{% endif %}>Mês {{m}}</option>{% endfor %}
                        </select>
                    </form>
                </div>

                <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom: 12px;">
                    <input id="buscaVendas" type="search" oninput="filtrarVendasDigitando()" placeholder="🔎 Buscar cliente, contrato..." style="flex:1; min-width:180px; padding:7px 10px; border:1px solid #cbd5e0; border-radius:6px; font-size:12px;">
                    <select id="filtroStatusVendas" onchange="filtrarVendasDigitando()" style="padding:7px; border:1px solid #cbd5e0; border-radius:6px; font-size:12px; background:#fff;"><option value="">Todos Status</option><option value="Ativo">Ativo</option><option value="Pendente">Pendente</option><option value="Cancelado">Cancelado</option></select>
                    <span id="contadorBusca" style="font-size:10px; color:#718096;"></span>
                </div>

                <div class="table-wrap">
                    <table id="tabelaVendas">
                        <thead>
                            <tr>
                                <th>Cliente</th>
                                <th>Plano</th>
                                <th>RIO</th>
                                <th>Data</th>
                                <th>Modelo</th>
                                <th>Qtd</th>
                                <th>Vendedor</th>
                                <th>Contrato</th>
                                <th>Status</th>
                                <th>Início</th>
                                <th>Final / Vencimento</th>
                                <th>Anexo</th>
                                <th class="nao-imprimir" style="text-align: center;">Ações</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for venda in vendas_lista %}
                            <tr class="linha-venda">
                                <td><b>{{ venda.cliente }}</b></td>
                                <td>{{ venda.plano }}</td>
                                <td>{{ venda.rio }}</td>
                                <td>{{ venda.data_venda }}</td>
                                <td>{{ venda.modelo }}</td>
                                <td style="text-align: center;">{{ venda.quantidade }}</td>
                                <td>{{ venda.vendedor }}</td>
                                <td>{{ venda.contrato }}</td>
                                <td><span style="background: #c6f6d5; color: #22543d; padding: 2px 5px; border-radius: 4px; font-weight: bold; font-size: 10px;">{{ venda.status }}</span></td>
                                <td>{{ venda.inicio }}</td>
                                <td>
                                    {{ venda.final }}<br>
                                    {% if venda.dias_restantes is not none %}
                                        {% if venda.dias_restantes < 0 %}
                                            <span class="badge-vencimento vencido">🔴 Vencido há {{ venda.dias_restantes | abs }}d</span><br><a href="{{ venda.whatsapp_url }}" target="_blank" class="btn-whatsapp">💬 WhatsApp</a>
                                        {% elif venda.dias_restantes <= 15 %}
                                            <span class="badge-vencimento proximo">⚠️ Vence em {{ venda.dias_restantes }}d</span><br><a href="{{ venda.whatsapp_url }}" target="_blank" class="btn-whatsapp">💬 WhatsApp</a>
                                        {% else %}
                                            <span class="badge-vencimento ok">🟢 Restam {{ venda.dias_restantes }}d</span>
                                        {% endif %}
                                    {% endif %}
                                </td>
                                <td>{% if venda.anexo1 %}<a href="{{ venda.anexo1 }}" target="_blank" style="color: #2b6cb0; font-size: 10px; font-weight: bold;">📄 Ver</a>{% else %}<span style="color:#a0aec0; font-size:10px;">Sem anexo</span>{% endif %}</td>
                                <td class="nao-imprimir" style="text-align: center; white-space: nowrap;">
                                    <div style="display: flex; gap: 4px; justify-content: center; align-items: center; flex-direction: column;">
                                        <a href="/vendas_pm/editar/{{ venda.id_linha }}" class="btn-edit" style="width: 100%;">Alterar</a>
                                        <form action="/vendas_pm/excluir/{{ venda.id_linha }}" method="POST" style="display:inline; margin: 0; width: 100%;" onsubmit="return confirm('Excluir?');">
                                            <button type="submit" class="btn btn-danger" style="width: 100%;">Excluir</button>
                                        </form>
                                    </div>
                                </td>
                            </tr>
                            {% else %}
                            <tr><td colspan="13" style="text-align: center; color: #718096; padding: 15px;">Nenhuma venda confirmada registrada.</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def login():
    erro = None
    if request.method == "POST":
        input_email = request.form.get("email", "").strip().lower()
        input_senha = request.form.get("senha", "")
        try:
            planilha = conectar_planilha()
            aba_usuarios = planilha.worksheet("Usuarios")
            dados = aba_usuarios.get_all_values()
            cabecalhos = [str(h).strip().upper() for h in dados[0]]
            linhas = dados[1:]
            usuario_encontrado = None
            for linha in linhas:
                row_dict = {cabecalhos[i]: linha[i] for i in range(min(len(cabecalhos), len(linha)))}
                if row_dict.get("EMAIL", "").lower() == input_email and row_dict.get("SENHA", "") == input_senha:
                    usuario_encontrado = row_dict
                    break
            if usuario_encontrado:
                session["logado"] = True
                session["nome"] = usuario_encontrado.get("NOME", "Usuário")
                session["perfil"] = usuario_encontrado.get("PERFIL", "COLABORADOR")
                def tem_p(*chaves):
                    return any(usuario_encontrado.get(c, "").upper() == "X" for c in chaves)
                session["permissoes"] = {
                    "pm_negocios": tem_p("NEGOCIOS_PM"),
                    "pm_vendas": tem_p("VENDAS_PM"),
                    "pm_gerenciador": tem_p("GERENCIADOR_PM", "GERENCIADO_PM"),
                    "loc_negocios": tem_p("NEGOCIOS_LOC"),
                    "loc_vendas": tem_p("VENDAS_LOC"),
                    "loc_gerenciador": tem_p("GERENCIADOR_LOC", "GERENCIADO_LOC"),
                    "cons_negocios": tem_p("NEGOCIOS_CONS"),
                    "cons_vendas": tem_p("VENDAS_CONS"),
                    "cons_gerenciador": tem_p("GERENCIADO_CONS"),
                }
                return redirect(url_for("dashboard"))
            else:
                erro = "E-mail ou Senha incorretos."
        except Exception as e:
            erro = f"Erro ao acessar planilha: {e}"
    return render_template_string(TEMPLATE_LOGIN, erro=erro)

@app.route("/dashboard")
def dashboard():
    if not session.get("logado"):
        return redirect(url_for("login"))
    ano = request.args.get("ano", str(datetime.now().year))
    periodo = request.args.get("periodo", "todos")
    try:
        planilha = conectar_planilha()
        dados = planilha.worksheet("Vendas_PM").get_all_values()
        anos = sorted({str(dt.year) for linha in dados[1:] if len(linha)>3 and (dt:=parse_data_flexivel(linha[3]))}, reverse=True)
        anos_disponiveis = anos or [str(datetime.now().year)]
        if ano not in anos_disponiveis: ano = anos_disponiveis[0]
        
        kpis, vendedores, regioes, meses, ultimos, neg_kpi, frios_lista = montar_dados_dashboard(planilha, ano, periodo)
    except Exception as e:
        return f"Erro ao carregar dashboard: {e}"
        
    meses_nomes = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}
    vendedores_ranking = sorted(vendedores.items(), key=lambda x: x[1]["comissao"], reverse=True)
    regioes_ranking = sorted(regioes.items(), key=lambda x: x[1]["comissao"], reverse=True)
    
    return render_template_string(
        TEMPLATE_DASHBOARD, 
        sidebar_html=gerar_sidebar(), 
        permissoes=session.get("permissoes",{}), 
        ano=ano, 
        periodo=periodo,
        anos_disponiveis=anos_disponiveis, 
        kpis=kpis, 
        vendedores=vendedores, 
        regioes=regioes, 
        vendedores_ranking=vendedores_ranking, 
        regioes_ranking=regioes_ranking, 
        meses=meses, 
        meses_nomes=meses_nomes, 
        ultimos=ultimos,
        neg_kpi=neg_kpi,
        frios_lista=frios_lista
    )

@app.route("/gerenciador_pm", methods=["GET", "POST"])
def gerenciador_pm():
    if not session.get("logado"):
        return redirect(url_for("login"))
    ano_vigente = str(datetime.now().year)
    filtro_ano = request.args.get("filtro_ano", ano_vigente)
    filtro_periodo = request.args.get("filtro_periodo", "todos")
    
    try:
        planilha = conectar_planilha()
        mapa_regioes = {}
        try:
            aba_u = planilha.worksheet("Usuarios")
            u_vals = aba_u.get_all_values()
            if len(u_vals) > 1:
                cab_u = [str(h).strip().upper() for h in u_vals[0]]
                idx_n = cab_u.index("NOME") if "NOME" in cab_u else 2
                idx_p = cab_u.index("PERFIL") if "PERFIL" in cab_u else 4
                for r in u_vals[1:]:
                    if len(r) > max(idx_n, idx_p):
                        nome_c = r[idx_n].strip()
                        perfil_c = r[idx_p].strip().upper()
                        if "AL" in perfil_c or "ALAGOAS" in perfil_c: mapa_regioes[nome_c] = "ALAGOAS (AL)"
                        elif "PE" in perfil_c or "PERNAMBUCO" in perfil_c: mapa_regioes[nome_c] = "PERNAMBUCO (PE)"
        except Exception:
            pass
        aba_vendas = planilha.worksheet("Vendas_PM")
        dados_vendas = aba_vendas.get_all_values()
        anos_encontrados = set()
        if len(dados_vendas) > 1:
            for linha in dados_vendas[1:]:
                if len(linha) > 3 and linha[3].strip():
                    partes = re.split(r'[-/.]', linha[3].strip())
                    for p in partes:
                        if len(p) == 4 and p.isdigit(): anos_encontrados.add(p)
                        elif len(p) == 2 and p.isdigit() and int(p) > 20: anos_encontrados.add("20" + p)
        anos_disponiveis = sorted([a for a in anos_encontrados if a and a.isdigit()], reverse=True)
        if not anos_disponiveis: anos_disponiveis = [ano_vigente]
        if filtro_ano not in anos_disponiveis and filtro_ano != "todos": filtro_ano = anos_disponiveis[0] if ano_vigente not in anos_disponiveis else ano_vigente

        relatorio_por_regiao = {}
        total_comissao_apm = 0
        total_vendas_qtd = 0
        total_rio_qtd = 0
        total_comissao_al = 0
        total_comissao_pe = 0

        resumo_apm = {
            "ALAGOAS (AL)": {"qtd_planos": 0, "qtd_rios": 0, "total_base": 0, "total_rio": 0, "total": 0},
            "PERNAMBUCO (PE)": {"qtd_planos": 0, "qtd_rios": 0, "total_base": 0, "total_rio": 0, "total": 0},
            "geral": 0
        }

        if len(dados_vendas) > 1:
            for idx_venda, linha in enumerate(dados_vendas[1:], start=2):
                while len(linha) < 13: linha.append("")
                cliente, plano, rio, data_venda_str = linha[0], linha[1], linha[2], linha[3].strip()
                if not data_venda_str: continue
                modelo = linha[4]
                try: quantidade = int(linha[5]) if linha[5].strip().isdigit() else 1
                except Exception: quantidade = 1
                vendedor, contrato = linha[6], linha[7]
                anexo1_val = linha[11].strip()

                ano_reg = ""
                mes_reg = None
                try:
                    partes_d = re.split(r'[-/.]', data_venda_str)
                    for p in partes_d:
                        if len(p) == 4 and p.isdigit(): ano_reg = p
                    if len(partes_d) >= 2 and partes_d[1].isdigit(): mes_reg = int(partes_d[1])
                except Exception: pass

                if not ano_reg: continue
                passou_ano = (filtro_ano == "todos" or ano_reg == filtro_ano)
                passou_periodo = True
                if passou_ano and filtro_periodo != "todos" and mes_reg:
                    if filtro_periodo == "s1" and not (1 <= mes_reg <= 6): passou_periodo = False
                    elif filtro_periodo == "s2" and not (7 <= mes_reg <= 12): passou_periodo = False
                    elif filtro_periodo.isdigit() and mes_reg != int(filtro_periodo): passou_periodo = False
                elif passou_ano and filtro_periodo != "todos" and not mes_reg: passou_periodo = False

                if not (passou_ano and passou_periodo): continue

                regiao = mapa_regioes.get(vendedor, "PERNAMBUCO (PE)")
                if "AL" in vendedor.upper() or "NARUHITO" in vendedor.upper() or "KLEBER" in vendedor.upper(): regiao = "ALAGOAS (AL)"

                if regiao not in relatorio_por_regiao:
                    relatorio_por_regiao[regiao] = {
                        "vendedores": {}, 
                        "total_regiao": 0
                    }

                unit_modelo, unit_rio, _ = calcular_comissao_vendedor(modelo, plano, rio, planilha)
                total_modelo_val = unit_modelo * quantidade
                total_rio_val = unit_rio * quantidade
                comissao_v_total = total_modelo_val + total_rio_val

                if regiao == "ALAGOAS (AL)":
                    total_comissao_al += comissao_v_total
                else:
                    total_comissao_pe += comissao_v_total

                _, _, apm_unit = calcular_comissao_apm(plano, rio)
                total_comissao_apm += apm_unit * quantidade
                total_vendas_qtd += quantidade
                
                apm_base_unitaria = 250
                apm_rio_unitaria = 150 if str(rio).strip() and str(rio).strip().lower() != "nenhum" else 0
                
                if regiao in resumo_apm:
                    resumo_apm[regiao]["qtd_planos"] += quantidade
                    resumo_apm[regiao]["total_base"] += (apm_base_unitaria * quantidade)
                    if apm_rio_unitaria > 0:
                        resumo_apm[regiao]["qtd_rios"] += quantidade
                        resumo_apm[regiao]["total_rio"] += (apm_rio_unitaria * quantidade)
                        total_rio_qtd += quantidade
                    resumo_apm[regiao]["total"] += ((apm_base_unitaria + apm_rio_unitaria) * quantidade)
                
                resumo_apm["geral"] += ((apm_base_unitaria + apm_rio_unitaria) * quantidade)

                if vendedor not in relatorio_por_regiao[regiao]["vendedores"]:
                    relatorio_por_regiao[regiao]["vendedores"][vendedor] = {"itens": [], "total_vendedor": 0}

                relatorio_por_regiao[regiao]["vendedores"][vendedor]["itens"].append({
                    "id_linha": idx_venda, "cliente": cliente, "modelo": modelo, "quantidade": quantidade,
                    "contrato": contrato, "unit_modelo": unit_modelo, "unit_rio": unit_rio,
                    "total_modelo": total_modelo_val, "total_rio": total_rio_val, 
                    "comissao_vendedor": comissao_v_total, "anexo1": anexo1_val
                })
                relatorio_por_regiao[regiao]["vendedores"][vendedor]["total_vendedor"] += comissao_v_total
                relatorio_por_regiao[regiao]["total_regiao"] += comissao_v_total
                
    except Exception as e:
        return f"Erro ao carregar gerenciador: {e}"

    return render_template_string(
        TEMPLATE_GERENCIADOR_PM, relatorio_por_regiao=relatorio_por_regiao, total_comissao_apm=total_comissao_apm,
        total_vendas_qtd=total_vendas_qtd, total_rio_qtd=total_rio_qtd, total_comissao_al=total_comissao_al, total_comissao_pe=total_comissao_pe,
        anos_disponiveis=anos_disponiveis, filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, permissoes=session.get("permissoes", {}), sidebar_html=gerar_sidebar(),
        obter_url_imagem_impressao=obter_url_imagem_impressao,
        resumo_apm=resumo_apm
    )

@app.route("/negocios_pm", methods=["GET", "POST"])
def negocios_pm():
    if not session.get("logado"): return redirect(url_for("login"))
    data_hoje_input = datetime.now().strftime("%Y-%m-%d")
    ano_vigente = str(datetime.now().year)
    filtro_ano = request.args.get("filtro_ano", request.form.get("filtro_ano", ""))
    filtro_periodo = request.args.get("filtro_periodo", request.form.get("filtro_periodo", "todos"))
    filtro_temp = request.args.get("filtro_temp", request.form.get("filtro_temp", "todos"))
    filtro_vendedor = request.args.get("filtro_vendedor", request.form.get("filtro_vendedor", "todos"))
    mensagem = request.args.get("mensagem", "")

    try:
        planilha = conectar_planilha()
        def extrair_coluna_especifica(nome_aba, col_index):
            try:
                aba_aux = planilha.worksheet(nome_aba)
                valores = aba_aux.col_values(col_index)
                return [v.strip() for v in valores[1:] if v.strip()]
            except Exception: return []

        modelos_lista = extrair_coluna_especifica("Modelos", 4)
        pm_lista = extrair_coluna_especifica("PM", 2)
        rio_lista = extrair_coluna_especifica("RIO", 2)
        consultores_lista = []
        try:
            aba_usuarios = planilha.worksheet("Usuarios")
            dados_usuarios = aba_usuarios.get_all_values()
            if len(dados_usuarios) > 1:
                cab_u = [str(h).strip().upper() for h in dados_usuarios[0]]
                idx_nome = cab_u.index("NOME") if "NOME" in cab_u else 2
                idx_perfil = cab_u.index("PERFIL") if "PERFIL" in cab_u else 4
                for u_linha in dados_usuarios[1:]:
                    if len(u_linha) > max(idx_nome, idx_perfil):
                        perfil_val = u_linha[idx_perfil].strip().upper()
                        if "CONSULTOR PE" in perfil_val or "CONSULTOR AL" in perfil_val:
                            nome_c = u_linha[idx_nome].strip()
                            if nome_c and nome_c not in consultores_lista: consultores_lista.append(nome_c)
            consultores_lista.sort()
        except Exception: consultores_lista = []

        aba_negocios = planilha.worksheet("Negocios_PM")
        if request.method == "POST":
            acao = request.form.get("acao")
            temperatura = request.form.get("temperatura")
            data_raw = request.form.get("data", "").strip()
            data = data_raw
            if data_raw:
                if "-" in data_raw:
                    p_iso = data_raw.split("-")
                    if len(p_iso) == 3: data = f"{p_iso[2]}/{p_iso[1]}/{p_iso[0]}"
                else:
                    p_d = re.split(r'[-/.]', data_raw)
                    if len(p_d) == 3:
                        d, m, a = p_d[0].zfill(2), p_d[1].zfill(2), p_d[2]
                        if len(a) == 2: a = "20" + a
                        data = f"{d}/{m}/{a}"
            vendedor, cliente, modelo, plano, rio, contato, telefone, comentarios = (
                request.form.get("vendedor"), request.form.get("cliente"), request.form.get("modelo"),
                request.form.get("plano"), request.form.get("rio"), request.form.get("contato"),
                request.form.get("telefone"), request.form.get("comentarios")
            )
            if acao == "adicionar":
                aba_negocios.append_row([temperatura, data, vendedor, cliente, modelo, plano, rio, contato, telefone, comentarios])
                msg = "Negociação cadastrada com sucesso!"
            elif acao == "editar":
                linha_id = int(request.form.get("linha_id"))
                aba_negocios.update(f"A{linha_id}:J{linha_id}", [[temperatura, data, vendedor, cliente, modelo, plano, rio, contato, telefone, comentarios]])
                msg = "Negociação alterada com sucesso!"
            return redirect(url_for("negocios_pm", filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, filtro_temp=filtro_temp, filtro_vendedor=filtro_vendedor, mensagem=msg))

        dados_brutos = aba_negocios.get_all_values()
        anos_encontrados = set()
        if len(dados_brutos) > 1:
            for linha in dados_brutos[1:]:
                if len(linha) > 1 and linha[1].strip():
                    val_data = linha[1].strip()
                    match_ano = re.search(r'\b(20\d{2})\b', val_data)
                    if match_ano: anos_encontrados.add(match_ano.group(1))
        
        anos_disponiveis = sorted([a for a in anos_encontrados if a and a.isdigit()], reverse=True)
        if not anos_disponiveis: anos_disponiveis = [ano_vigente]
            
        if not request.args.get("filtro_ano") and not request.form.get("filtro_ano"):
            filtro_ano = ano_vigente if ano_vigente in anos_disponiveis else anos_disponiveis[0]
        elif not filtro_ano or filtro_ano not in anos_disponiveis:
            filtro_ano = anos_disponiveis[0]

        dados_com_id = []
        total_fechados = total_super_quente = total_quentes = total_mornos = total_perdida = total_frios = 0
        
        if len(dados_brutos) > 1:
            for idx, linha in enumerate(dados_brutos[1:], start=2):
                while len(linha) < 10: linha.append("")
                temp_val = linha[0].strip().title()
                data_str = linha[1].strip()
                vendedor_val = linha[2].strip()
                
                data_iso = data_str
                if "/" in data_str:
                    p_data = data_str.split("/")
                    if len(p_data) == 3:
                        d_i, m_i, a_i = p_data[0].zfill(2), p_data[1].zfill(2), p_data[2]
                        if len(a_i) == 2: a_i = "20" + a_i
                        data_iso = f"{a_i}-{m_i}-{d_i}"

                ano_registro = ""
                mes_registro = None
                if data_str:
                    try:
                        partes = re.split(r'[-/.]', data_str)
                        for p in partes:
                            if len(p) == 4 and p.isdigit(): ano_registro = p
                            elif len(p) == 2 and p.isdigit() and int(p) > 20: ano_registro = "20" + p
                        if len(partes) >= 2 and partes[1].isdigit(): mes_registro = int(partes[1])
                    except Exception: pass

                passou_ano = (filtro_ano == "todos" or ano_registro == filtro_ano)
                passou_periodo = True
                if passou_ano and filtro_periodo != "todos" and mes_registro:
                    if filtro_periodo == "s1" and not (1 <= mes_registro <= 6): passou_periodo = False
                    elif filtro_periodo == "s2" and not (7 <= mes_registro <= 12): passou_periodo = False
                    elif filtro_periodo.isdigit() and mes_registro != int(filtro_periodo): passou_periodo = False
                elif passou_ano and filtro_periodo != "todos" and not mes_registro: passou_periodo = False

                passou_vendedor = (filtro_vendedor == "todos" or vendedor_val.lower() == filtro_vendedor.lower())

                if passou_ano and passou_periodo and passou_vendedor:
                    if temp_val == "Fechado": total_fechados += 1
                    elif temp_val == "Super Quente": total_super_quente += 1
                    elif temp_val == "Quente": total_quentes += 1
                    elif temp_val == "Morno": total_mornos += 1
                    elif temp_val == "Perdida": total_perdida += 1
                    elif temp_val == "Frio": total_frios += 1

                if passou_ano and passou_periodo and passou_vendedor:
                    if filtro_temp == "todos" or temp_val.lower() == filtro_temp.lower():
                        dados_com_id.append({
                            "id_linha": idx, "temperatura": linha[0], "data": data_str, "data_iso": data_iso,
                            "vendedor": vendedor_val, "cliente": linha[3], "modelo": linha[4], "plano": linha[5],
                            "rio": linha[6], "contato": linha[7], "telefone": linha[8], "comentarios": linha[9]
                        })
                        
        def chave_ordenacao(item):
            try:
                p = re.split(r'[-/.]', item["data"])
                if len(p) == 3:
                    d, m, a = p[0].zfill(2), p[1].zfill(2), p[2]
                    if len(a) == 2: a = "20" + a
                    return f"{a}{m}{d}"
            except Exception: pass
            return "00000000"
        dados_com_id.sort(key=chave_ordenacao, reverse=True)
    except Exception as e:
        return f"Erro ao carregar dados: {e}"

    total_geral = total_fechados + total_super_quente + total_quentes + total_mornos + total_perdida + total_frios
    return render_template_string(
        TEMPLATE_NEGOCIOS_PM, dados=dados_com_id, total_geral=total_geral, total_fechados=total_fechados,
        total_super_quente=total_super_quente, total_quentes=total_quentes, total_mornos=total_mornos,
        total_perdida=total_perdida, total_frios=total_frios, permissoes=session.get("permissoes", {}), sidebar_html=gerar_sidebar(),
        modelos_lista=modelos_lista, pm_lista=pm_lista, rio_lista=rio_lista, consultores_lista=consultores_lista,
        anos_disponiveis=anos_disponiveis, filtro_ano=filtro_ano, filtro_periodo=filtro_periodo,
        filtro_temp=filtro_temp, filtro_vendedor=filtro_vendedor, data_hoje_input=data_hoje_input, mensagem=mensagem
    )

@app.route("/vendas_pm", methods=["GET", "POST"])
def vendas_pm():
    if not session.get("logado"): return redirect(url_for("login"))
    mensagem = request.args.get("mensagem", "")
    ano_vigente = str(datetime.now().year)
    filtro_ano = request.args.get("filtro_ano", ano_vigente)
    filtro_periodo = request.args.get("filtro_periodo", "todos")
    negocio_alvo = session.get("fechando_negocio_alvo", None)
    venda_editando = session.get("venda_editando_alvo", None)
    modo_edicao = venda_editando is not None

    try:
        planilha = conectar_planilha()
        aba_vendas = planilha.worksheet("Vendas_PM")
        def extrair_coluna_especifica(nome_aba, col_index):
            try:
                aba_aux = planilha.worksheet(nome_aba)
                valores = aba_aux.col_values(col_index)
                return [v.strip() for v in valores[1:] if v.strip()]
            except Exception: return []
        pm_lista = extrair_coluna_especifica("PM", 2)
        rio_lista = extrair_coluna_especifica("RIO", 2)
        dados_vendas = aba_vendas.get_all_values()
        anos_encontrados = set()
        if len(dados_vendas) > 1:
            for linha in dados_vendas[1:]:
                if len(linha) > 3 and linha[3].strip():
                    partes = re.split(r'[-/.]', linha[3].strip())
                    for p in partes:
                        if len(p) == 4 and p.isdigit(): anos_encontrados.add(p)
                        elif len(p) == 2 and p.isdigit() and int(p) > 20: anos_encontrados.add("20" + p)
        anos_disponiveis = sorted([a for a in anos_encontrados if a and a.isdigit()], reverse=True)
        if not anos_disponiveis: anos_disponiveis = [ano_vigente]
        if filtro_ano not in anos_disponiveis and filtro_ano != "todos": filtro_ano = anos_disponiveis[0] if ano_vigente not in anos_disponiveis else ano_vigente

        if request.method == "POST":
            acao = request.form.get("acao")
            if acao == "salvar_venda":
                cliente, plano, rio, data_venda, modelo, quantidade, vendedor, contrato, status, inicio, final = (
                    request.form.get("cliente"), request.form.get("plano"), request.form.get("rio"),
                    request.form.get("data_venda"), request.form.get("modelo"), request.form.get("quantidade"),
                    request.form.get("vendedor"), request.form.get("contrato"), request.form.get("status"),
                    request.form.get("inicio"), request.form.get("final")
                )
                caminho_anexo1 = ""
                if 'anexo1' in request.files:
                    file1 = request.files['anexo1']
                    if file1 and file1.filename != '':
                        valido1, nome1 = validar_upload_imagem(file1)
                        if not valido1: return redirect(url_for("vendas_pm", filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, mensagem=nome1))
                        ext1 = os.path.splitext(nome1)[1].lower()
                        filename1 = nome_seguro_anexo(contrato, quantidade, modelo, cliente, "Anexo", ext1)
                        local_path1 = os.path.join(app.config['UPLOAD_FOLDER'], filename1)
                        file1.save(local_path1)
                        caminho_anexo1 = salvar_arquivo_google_drive(local_path1, filename1)

                aba_vendas.append_row([cliente, plano, rio, data_venda, modelo, quantidade, vendedor, contrato, status, inicio, final, caminho_anexo1, ""])
                id_orig = session.pop("fechando_negocio_alvo_id", None)
                if id_orig:
                    try:
                        aba_neg = planilha.worksheet("Negocios_PM")
                        aba_neg.delete_rows(int(id_orig))
                    except Exception: pass
                session.pop("fechando_negocio_alvo", None)
                return redirect(url_for("vendas_pm", filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, mensagem="Venda registrada com sucesso!"))

            elif acao == "salvar_edicao_venda":
                linha_id = int(request.form.get("linha_id"))
                cliente, plano, rio, data_venda, modelo, quantidade, vendedor, contrato, status, inicio, final = (
                    request.form.get("cliente"), request.form.get("plano"), request.form.get("rio"),
                    request.form.get("data_venda"), request.form.get("modelo"), request.form.get("quantidade"),
                    request.form.get("vendedor"), request.form.get("contrato"), request.form.get("status"),
                    request.form.get("inicio"), request.form.get("final")
                )
                linha_atual = aba_vendas.row_values(linha_id)
                while len(linha_atual) < 13: linha_atual.append("")
                caminho_anexo1 = linha_atual[11]
                if 'anexo1' in request.files:
                    file1 = request.files['anexo1']
                    if file1 and file1.filename != '':
                        valido1, nome1 = validar_upload_imagem(file1)
                        if not valido1: return redirect(url_for("vendas_pm", filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, mensagem=nome1))
                        ext1 = os.path.splitext(nome1)[1].lower()
                        filename1 = nome_seguro_anexo(contrato, quantidade, modelo, cliente, "Anexo", ext1)
                        local_path1 = os.path.join(app.config['UPLOAD_FOLDER'], filename1)
                        file1.save(local_path1)
                        caminho_anexo1 = salvar_arquivo_google_drive(local_path1, filename1)

                aba_vendas.update(f"A{linha_id}:M{linha_id}", [[cliente, plano, rio, data_venda, modelo, quantidade, vendedor, contrato, status, inicio, final, caminho_anexo1, ""]])
                session.pop("venda_editando_alvo", None)
                return redirect(url_for("vendas_pm", filtro_ano=filtro_ano, filtro_periodo=filtro_periodo, mensagem="Venda alterada com sucesso!"))

        vendas_lista = []
        total_proximos = total_vencidos = total_veiculos = 0
        hoje = date.today()

        if len(dados_vendas) > 1:
            for idx, linha in enumerate(dados_vendas[1:], start=2):
                while len(linha) < 13: linha.append("")
                data_venda_str = linha[3].strip()
                if not data_venda_str: continue
                ano_reg = ""
                mes_reg = None
                try:
                    partes_d = re.split(r'[-/.]', data_venda_str)
                    for p in partes_d:
                        if len(p) == 4 and p.isdigit(): ano_reg = p
                    if len(partes_d) >= 2 and partes_d[1].isdigit(): mes_reg = int(partes_d[1])
                except Exception: pass

                if not ano_reg: continue
                passou_ano = (filtro_ano == "todos" or ano_reg == filtro_ano)
                passou_periodo = True
                if passou_ano and filtro_periodo != "todos" and mes_reg:
                    if filtro_periodo == "s1" and not (1 <= mes_reg <= 6): passou_periodo = False
                    elif filtro_periodo == "s2" and not (7 <= mes_reg <= 12): passou_periodo = False
                    elif filtro_periodo.isdigit() and mes_reg != int(filtro_periodo): passou_periodo = False
                elif passou_ano and filtro_periodo != "todos" and not mes_reg: passou_periodo = False

                if not (passou_ano and passou_periodo): continue

                try: qtd_v = int(linha[5]) if linha[5].strip().isdigit() else 1
                except Exception: qtd_v = 1
                total_veiculos += qtd_v

                data_final_str = linha[10].strip()
                dias_restantes = None
                final_iso = data_final_str
                whatsapp_url = ""
                
                if data_final_str:
                    try:
                        if "-" in data_final_str:
                            dt_obj = datetime.strptime(data_final_str, "%Y-%m-%d").date()
                            final_iso = data_final_str
                        else:
                            dt_obj = datetime.strptime(data_final_str, "%d/%m/%Y").date()
                            final_iso = dt_obj.strftime("%Y-%m-%d")
                        
                        dias_restantes = (dt_obj - hoje).days
                        cli_nome = linha[0]
                        ct_num = linha[7]
                        
                        if dias_restantes < 0:
                            total_vencidos += 1
                            msg_wpp = f"Olá {cli_nome}, informamos que o seu Contrato nº {ct_num} venceu há {abs(dias_restantes)} dias."
                            whatsapp_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(msg_wpp)}"
                        elif 0 <= dias_restantes <= 15:
                            total_proximos += 1
                            msg_wpp = f"Olá {cli_nome}, lembrete: seu Contrato nº {ct_num} vence em {dias_restantes} dias ({data_final_str})."
                            whatsapp_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(msg_wpp)}"
                    except Exception: pass

                vendas_lista.append({
                    "id_linha": idx, "cliente": linha[0], "plano": linha[1], "rio": linha[2],
                    "data_venda": data_venda_str, "modelo": linha[4], "quantidade": qtd_v, "vendedor": linha[6],
                    "contrato": linha[7], "status": linha[8], "inicio": linha[9], "inicio_iso": linha[9] if "-" in linha[9] else "",
                    "final": linha[10], "final_iso": final_iso, "dias_restantes": dias_restantes, "anexo1": linha[11],
                    "whatsapp_url": whatsapp_url
                })
        vendas_lista.reverse()
        total_vendas = len(vendas_lista)
    except Exception as e:
        return f"Erro ao carregar vendas: {e}"

    return render_template_string(
        TEMPLATE_VENDAS_PM, vendas_lista=vendas_lista, total_vendas=total_vendas, total_veiculos=total_veiculos,
        total_proximos=total_proximos, total_vencidos=total_vencidos, filtro_ano=filtro_ano, filtro_periodo=filtro_periodo,
        anos_disponiveis=anos_disponiveis, negocio_alvo=negocio_alvo, venda_editando=venda_editando, modo_edicao=modo_edicao,
        pm_lista=pm_lista, rio_lista=rio_lista, permissoes=session.get("permissoes", {}), sidebar_html=gerar_sidebar(), mensagem=mensagem
    )

@app.route("/vendas_pm/fechar/<int:linha_id>")
def iniciar_fechamento_venda(linha_id: int):
    if not session.get("logado"): return redirect(url_for("login"))
    try:
        planilha = conectar_planilha()
        aba = planilha.worksheet("Negocios_PM")
        linha = aba.row_values(linha_id)
        while len(linha) < 10: linha.append("")
        session["fechando_negocio_alvo"] = {
            "id_linha": linha_id, "temperatura": linha[0], "data": linha[1], "vendedor": linha[2],
            "cliente": linha[3], "modelo": linha[4], "plano": linha[5], "rio": linha[6], "contato": linha[7],
            "telefone": linha[8], "comentarios": linha[9]
        }
        session["fechando_negocio_alvo_id"] = linha_id
        session.pop("venda_editando_alvo", None)
    except Exception as e:
        print(f"Erro: {e}")
    return redirect(url_for("vendas_pm"))

@app.route("/vendas_pm/editar/<int:linha_id>")
def iniciar_edicao_venda(linha_id: int):
    if not session.get("logado"): return redirect(url_for("login"))
    try:
        planilha = conectar_planilha()
        aba = planilha.worksheet("Vendas_PM")
        linha = aba.row_values(linha_id)
        while len(linha) < 13: linha.append("")
        session["venda_editando_alvo"] = {
            "id_linha": linha_id, "cliente": linha[0], "plano": linha[1], "rio": linha[2], "data_venda": linha[3],
            "modelo": linha[4], "quantidade": linha[5], "vendedor": linha[6], "contrato": linha[7], "status": linha[8],
            "inicio": linha[9], "inicio_iso": linha[9] if "-" in linha[9] else "", "final": linha[10],
            "final_iso": linha[10] if "-" in linha[10] else "", "anexo1": linha[11]
        }
        session.pop("fechando_negocio_alvo", None)
    except Exception as e:
        print(f"Erro: {e}")
    return redirect(url_for("vendas_pm"))

@app.route("/vendas_pm/excluir/<int:linha_id>", methods=["POST"])
def excluir_venda_pm(linha_id: int):
    if not session.get("logado"): return redirect(url_for("login"))
    try:
        planilha = conectar_planilha()
        aba = planilha.worksheet("Vendas_PM")
        aba.delete_rows(linha_id)
    except Exception as e: print(f"Erro: {e}")
    return redirect(url_for("vendas_pm", mensagem="Venda excluída com sucesso!"))

@app.route("/negocios_pm/excluir/<int:linha_id>", methods=["POST"])
def excluir_negocio_pm(linha_id: int):
    if not session.get("logado"): return redirect(url_for("login"))
    try:
        planilha = conectar_planilha()
        aba = planilha.worksheet("Negocios_PM")
        aba.delete_rows(linha_id)
    except Exception as e: print(f"Erro: {e}")
    return redirect(url_for("negocios_pm", mensagem="Registro excluído com sucesso!"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)