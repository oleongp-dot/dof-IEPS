import datetime
import os
import re
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import plotly.graph_objects as gr
from plotly.subplots import make_subplots
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

warnings.filterwarnings("ignore")

# ==========================================
# INICIALIZACIÓN DE LA APP
# ==========================================
app = FastAPI()

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ==========================================
# GESTIÓN DE CACHÉ
# ==========================================
_cache = {"datos": None, "timestamp": None}
_cache_lock = threading.Lock()
CACHE_MINUTOS = 30


def cache_valido():
    with _cache_lock:
        if _cache["datos"] is None:
            return False
        delta = datetime.datetime.now() - _cache["timestamp"]
        return delta.total_seconds() < CACHE_MINUTOS * 60


def guardar_cache(datos):
    with _cache_lock:
        _cache["datos"] = datos
        _cache["timestamp"] = datetime.datetime.now()


def obtener_cache():
    with _cache_lock:
        return _cache["datos"]


# ==========================================
# SCRAPERS DE EXTRACCIÓN Y PROCESAMIENTO
# ==========================================
def parsear_fecha_texto(texto_fecha, anio_respaldo):
    meses = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
    }
    try:
        texto_fecha = texto_fecha.lower().strip()
        match = re.search(r"(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?", texto_fecha)
        if match:
            dia = int(match.group(1))
            mes = meses.get(match.group(2), 1)
            anio = int(match.group(3)) if match.group(3) else int(anio_respaldo)
            return datetime.datetime(anio, mes, dia)
    except:
        pass
    return None


def extraer_ieps(url, fecha_str):
    try:
        respuesta = requests.get(url, headers=headers, verify=False, timeout=8)
        soup = BeautifulSoup(respuesta.text, "html.parser")
        texto = soup.get_text()

        # Extraer vigencia
        vigencia = re.search(
            r"periodo comprendido del (.+?)\s+al\s+(.+?\d{4})", texto, re.IGNORECASE
        )
        
        vigencia_str = "No disponible"
        fecha_fin_real = None
        
        if vigencia:
            texto_inicio = vigencia.group(1).strip()
            texto_fin = vigencia.group(2).strip()
            vigencia_str = f"del {texto_inicio} al {texto_fin}"
            
            anio_base = fecha_str.split("/")[-1]
            fecha_fin_real_dt = parsear_fecha_texto(texto_fin, anio_base)
            if fecha_fin_real_dt:
                fecha_fin_real = fecha_fin_real_dt.strftime("%d/%m/%Y")

        valores = {"regular": 0.0, "premium": 0.0, "diesel": 0.0}

        # Búsqueda estricta obligatoria del Artículo Tercero (evita Artículo Segundo)
        match_inicio = re.search(r"ARTÍCULO\s+TERCERO", texto, re.IGNORECASE)
        if not match_inicio:
            match_inicio = re.search(r"ARTÍCULO\s+PRIMERO", texto, re.IGNORECASE)

        if match_inicio:
            inicio = match_inicio.start()
            bloque_base = texto[inicio : inicio + 3000]
            
            for linea in bloque_base.split("\n"):
                linea_clean = linea.strip()
                if not linea_clean:
                    continue

                if re.search(r"Gasolina\s+menor\s+a\s+91\s+octanos", linea_clean, re.I):
                    m = re.search(r"(?:\$)?\s*([\d]+\.[\d]{4})", linea_clean)
                    if m: valores["regular"] = float(m.group(1))

                elif re.search(r"Gasolina\s+mayor\s+o\s+igual\s+a\s+91\s+octanos", linea_clean, re.I):
                    m = re.search(r"(?:\$)?\s*([\d]+\.[\d]{4})", linea_clean)
                    if m: valores["premium"] = float(m.group(1))

                elif re.search(r"Diésel", linea_clean, re.I):
                    m = re.search(r"(?:\$)?\s*([\d]+\.[\d]{4})", linea_clean)
                    if m: valores["diesel"] = float(m.group(1))

            if valores["premium"] == 0.0 or valores["diesel"] == 0.0:
                cifras = re.findall(r"\b\d+\.\d{4}\b", bloque_base)
                if len(cifras) >= 3:
                    valores["regular"] = float(cifras[0])
                    valores["premium"] = float(cifras[1])
                    valores["diesel"] = float(cifras[2])

        # Búsqueda de Estímulo Adicional (Artículo Cuarto)
        valores["adic_regular"] = 0.0
        valores["adic_premium"] = 0.0
        valores["adic_diesel"] = 0.0

        idx_art4 = re.search(r"ARTÍCULO\s+CUARTO", texto, re.IGNORECASE)
        if idx_art4:
            bloque_art4 = texto[idx_art4.start() : idx_art4.start() + 2500]
            
            for linea in bloque_art4.split("\n"):
                linea_clean = linea.strip()
                if not linea_clean:
                    continue

                if re.search(r"Gasolina\s+menor\s+a\s+91\s+octanos", linea_clean, re.I):
                    m = re.search(r"(?:\$)?\s*([\d]+\.[\d]{4})", linea_clean)
                    if m: valores["adic_regular"] = float(m.group(1))

                elif re.search(r"Gasolina\s+mayor\s+o\s+igual\s+a\s+91\s+octanos", linea_clean, re.I):
                    m = re.search(r"(?:\$)?\s*([\d]+\.[\d]{4})", linea_clean)
                    if m: valores["adic_premium"] = float(m.group(1))

                elif re.search(r"Diésel", linea_clean, re.I):
                    m = re.search(r"(?:\$)?\s*([\d]+\.[\d]{4})", linea_clean)
                    if m: valores["adic_diesel"] = float(m.group(1))

            if valores["adic_premium"] == 0.0 and valores["adic_diesel"] == 0.0:
                cifras_art4 = re.findall(r"\b\d+\.\d{4}\b", bloque_art4)
                if len(cifras_art4) >= 3:
                    valores["adic_regular"] = float(cifras_art4[0])
                    valores["adic_premium"] = float(cifras_art4[1])
                    valores["adic_diesel"] = float(cifras_art4[2])

        return {
            "fecha": fecha_str,
            "vigencia": vigencia_str,
            "fecha_fin_real": fecha_fin_real,
            **valores,
        }
    except:
        pass
    return None


def extraer_tipo_cambio(url, fecha_str):
    try:
        respuesta = requests.get(url, headers=headers, verify=False, timeout=8)
        soup = BeautifulSoup(respuesta.text, "html.parser")
        texto = soup.get_text()

        match = re.search(
            r"(?:equivalencia|tipo de cambio).*?(\d{2}\.\d{4})",
            texto,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            match = re.search(r"\b(\d{2}\.\d{4})\b", texto)

        if match:
            valor = float(
                match.group(1) if len(match.groups()) > 0 else match.group()
            )
            return {"fecha": fecha_str, "valor": valor}
    except:
        pass
    return None


def buscar_dia(fecha):
    day = fecha.strftime("%d")
    month = fecha.strftime("%m")
    year = fecha.strftime("%Y")
    fecha_str = f"{day}/{month}/{year}"

    if fecha.weekday() >= 5:
        return None

    tipo_cambio_url = None
    ieps_url = None

    for edicion in ["MAT", "VES"]:
        url = f"https://dof.gob.mx/index.php?year={year}&month={month}&day={day}&edicion={edicion}"
        try:
            respuesta = requests.get(url, headers=headers, verify=False, timeout=8)
            soup = BeautifulSoup(respuesta.text, "html.parser")

            for pub in soup.find_all("a"):
                texto = pub.text.lower().strip()
                enlace = pub.get("href", "")
                
                if not texto or not enlace or "nota_detalle.php" not in enlace:
                    continue

                if enlace and not enlace.startswith("http"):
                    enlace = f"https://dof.gob.mx/{enlace}"

                if "tipo de cambio" in texto and not tipo_cambio_url:
                    tipo_cambio_url = enlace
                elif any(kw in texto for kw in ["estímulos fiscales", "estimulos fiscales", "cuotas disminuidas", "porcentajes y los montos"]) and not ieps_url:
                    ieps_url = enlace
        except:
            pass

    if not tipo_cambio_url and not ieps_url:
        return None

    resultado_dia = {"fecha": fecha_str, "tc": None, "ieps": None}
    if tipo_cambio_url:
        resultado_dia["tc"] = extraer_tipo_cambio(tipo_cambio_url, fecha_str)
    if ieps_url:
        resultado_dia["ieps"] = extraer_ieps(ieps_url, fecha_str)

    return resultado_dia


def calcular_variacion(lista_valores):
    if len(lista_valores) < 2:
        return {"texto": "Sin histórico", "tipo": "neutral", "valor": 0}
    
    diferencia = lista_valores[-1] - lista_valores[-2]
    
    if diferencia > 0:
        return {
            "texto": f"+${diferencia:.4f}",
            "tipo": "sube",
            "valor": diferencia,
        }
    elif diferencia < 0:
        return {
            "texto": f"-${abs(diferencia):.4f}",
            "tipo": "baja",
            "valor": diferencia,
        }
    return {"texto": "Sin cambios", "tipo": "neutral", "valor": 0}


# ==========================================
# GENERACIÓN DE GRÁFICA INTERACTIVA PLOTLY
# ==========================================
def generar_grafica_json(datos):
    fechas_tc = datos.get("fechas_tc", [])
    valores_tc = datos.get("valores_tc", [])
    fechas_ieps = datos.get("fechas_ieps", [])
    vals_regular = datos.get("vals_regular", [])
    vals_premium = datos.get("vals_premium", [])
    vals_diesel = datos.get("vals_diesel", [])
    vals_adic_regular = datos.get("vals_adic_regular", [])
    vals_adic_premium = datos.get("vals_adic_premium", [])
    vals_adic_diesel = datos.get("vals_adic_diesel", [])
    fechas_fin_ieps = datos.get("fechas_fin_ieps", [])

    puntos_tc = []
    for f, v in zip(fechas_tc, valores_tc):
        try:
            f_date = datetime.datetime.strptime(f, "%d/%m/%Y")
            puntos_tc.append((f_date, v))
        except:
            continue
    puntos_tc.sort(key=lambda x: x[0])

    puntos_ieps = []
    for f, f_fin, reg, prem, dies, ad_reg, ad_prem, ad_dies in zip(
        fechas_ieps, fechas_fin_ieps, vals_regular, vals_premium, vals_diesel,
        vals_adic_regular, vals_adic_premium, vals_adic_diesel
    ):
        try:
            f_date = datetime.datetime.strptime(f, "%d/%m/%Y")
            if f_date.weekday() == 4:
                f_date = f_date + datetime.timedelta(days=1)
            
            val_r = -ad_reg if reg == 0.0 and ad_reg > 0 else reg
            val_p = -ad_prem if prem == 0.0 and ad_prem > 0 else prem
            val_d = -ad_dies if dies == 0.0 and ad_dies > 0 else dies

            puntos_ieps.append((f_date, f_fin, val_r, val_p, val_d))
        except:
            continue
            
    puntos_ieps.sort(key=lambda x: x[0])

    if puntos_ieps and puntos_ieps[-1][1]:
        try:
            ff_str = puntos_ieps[-1][1]
            dt_fin_real = datetime.datetime.strptime(ff_str, "%d/%m/%Y")
            ult_m, ult_p, ult_d = puntos_ieps[-1][2], puntos_ieps[-1][3], puntos_ieps[-1][4]
            puntos_ieps.append((dt_fin_real, ff_str, ult_m, ult_p, ult_d))
        except:
            pass

    if not puntos_tc and not puntos_ieps:
        return None

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=False,
        vertical_spacing=0.15,
        subplot_titles=("💱 Tipo de Cambio USD/MXN", "⛽ IEPS Combustibles (Pesos/Litro)")
    )

    if puntos_tc:
        fx_tc = [p[0].strftime("%Y-%m-%d") for p in puntos_tc]
        vy_tc = [p[1] for p in puntos_tc]
        
        fig.add_trace(
            gr.Scatter(
                x=fx_tc, y=vy_tc,
                mode='lines+markers',
                name='USD/MXN',
                line=dict(color='#58A6FF', width=2.5),
                marker=dict(size=6),
                fill='tozeroy',
                fillcolor='rgba(88, 166, 255, 0.02)'
            ),
            row=1, col=1
        )

    if puntos_ieps:
        fx_ieps = [p[0].strftime("%Y-%m-%d") for p in puntos_ieps]
        vy_regular = [p[2] for p in puntos_ieps]
        vy_premium = [p[3] for p in puntos_ieps]
        vy_diesel = [p[4] for p in puntos_ieps]
        
        fig.add_trace(
            gr.Scatter(
                x=fx_ieps, y=vy_regular, 
                mode='lines+markers', 
                name='Regular (<91 oct)', 
                line=dict(color='#3FB950', width=2.5, shape='hv'), 
                marker=dict(size=6)
            ),
            row=2, col=1
        )
        fig.add_trace(
            gr.Scatter(
                x=fx_ieps, y=vy_premium, 
                mode='lines+markers', 
                name='Premium (≥91 oct)', 
                line=dict(color='#FF7B72', width=2.5, shape='hv'), 
                marker=dict(size=6)
            ),
            row=2, col=1
        )
        fig.add_trace(
            gr.Scatter(
                x=fx_ieps, y=vy_diesel, 
                mode='lines+markers', 
                name='Diésel', 
                line=dict(color='#FFFFFF', width=2.5, shape='hv'), 
                marker=dict(size=6)
            ),
            row=2, col=1
        )

    fig.update_layout(
        font=dict(color="#E6EDF3", family="Segoe UI, sans-serif"),
        paper_bgcolor="#0D1117",
        plot_bgcolor="#161B22",
        height=700,
        showlegend=True,
        legend=dict(bgcolor="#21262D", bordercolor="#30363D", font=dict(size=10)),
        margin=dict(l=60, r=40, t=50, b=50)
    )

    fig.update_xaxes(
        type='category',
        showgrid=True,
        gridcolor='rgba(139, 148, 158, 0.08)',
        tickfont=dict(size=9, color="#8B949E"),
        linecolor="#30363D",
        tickangle=-45
    )

    if puntos_tc:
        valores = [p[1] for p in puntos_tc]
        min_val = min(valores)
        max_val = max(valores)
        margen = max((max_val - min_val) * 0.05, 0.005)
        
        fig.update_yaxes(
            title_text="Pesos por dólar",
            row=1, col=1,
            autorange=False, 
            range=[min_val - margen, max_val + margen],
            showgrid=True,
            gridcolor='rgba(139, 148, 158, 0.08)',
            tickfont=dict(size=10, color="#8B949E"),
            linecolor="#30363D",
            tickformat=".4f"
        )

    if puntos_ieps:
        todos_ieps = []
        for p in puntos_ieps:
            todos_ieps.extend([p[2], p[3], p[4]])
        min_ieps = min(todos_ieps)
        max_ieps = max(todos_ieps)
        margen_ieps = max(abs(max_ieps - min_ieps) * 0.05, 0.05)
        
        fig.update_yaxes(
            title_text="Pesos por litro",
            row=2, col=1,
            autorange=False,
            range=[min_ieps - margen_ieps, max_ieps + margen_ieps],
            showgrid=True,
            gridcolor='rgba(139, 148, 158, 0.08)',
            zeroline=True,
            zerolinecolor='rgba(139, 148, 158, 0.3)',
            zerolinewidth=1.5,
            gridwidth=1,
            tickfont=dict(size=10, color="#8B949E"),
            linecolor="#30363D",
            tickformat=".4f"
        )
        
        fig.add_hline(
            y=0, 
            line_dash="dot", 
            line_color="rgba(139, 148, 158, 0.25)", 
            line_width=1.5,
            row=2, col=1
        )

    return fig.to_json()


# ==========================================
# MOTOR DEL SCRAPER (HISTÓRICO 38 DÍAS)
# ==========================================
def run_scraper():
    hoy = datetime.datetime.now()
    resultados_dias = []
    
    dias_a_revisar = [hoy - datetime.timedelta(days=i) for i in range(38)]
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(buscar_dia, fecha): fecha for fecha in dias_a_revisar}
        for future in as_completed(futures):
            try:
                resultado = future.result(timeout=10)
                if resultado and (resultado.get("tc") or resultado.get("ieps")):
                    resultados_dias.append(resultado)
            except Exception:
                pass

    try:
        resultados_dias.sort(key=lambda x: datetime.datetime.strptime(x["fecha"], "%d/%m/%Y"))
    except:
        pass

    fechas_tc, valores_tc = [], []
    fechas_ieps, fechas_fin_ieps = [], []
    vals_regular, vals_premium, vals_diesel = [], [], []
    vals_adic_regular, vals_adic_premium, vals_adic_diesel = [], [], []
    vigencias_texto = []
    ultima_fecha_tc = "No disponible"
    ultima_vigencia = "No disponible"

    for dia in resultados_dias:
        if dia.get("tc") and dia["tc"].get("valor"):
            fechas_tc.append(dia["tc"]["fecha"])
            valores_tc.append(dia["tc"]["valor"])
            ultima_fecha_tc = dia["tc"]["fecha"]
            
        if dia.get("ieps") and dia["ieps"].get("regular") is not None:
            fechas_ieps.append(dia["ieps"]["fecha"])
            fechas_fin_ieps.append(dia["ieps"].get("fecha_fin_real"))
            
            vals_regular.append(dia["ieps"].get("regular", 0.0))
            vals_premium.append(dia["ieps"].get("premium", 0.0))
            vals_diesel.append(dia["ieps"].get("diesel", 0.0))
            
            vals_adic_regular.append(dia["ieps"].get("adic_regular", 0.0))
            vals_adic_premium.append(dia["ieps"].get("adic_premium", 0.0))
            vals_adic_diesel.append(dia["ieps"].get("adic_diesel", 0.0))
            
            vigencias_texto.append(dia["ieps"].get("vigencia", "No disponible"))
            ultima_vigencia = dia["ieps"].get("vigencia", "No disponible")

    datos = {
        "fecha_consulta": hoy.strftime("%d/%m/%Y %H:%M"),
        "fechas_tc": fechas_tc,
        "valores_tc": valores_tc,
        "fechas_ieps": fechas_ieps,
        "fechas_fin_ieps": fechas_fin_ieps,
        "vals_regular": vals_regular,
        "vals_premium": vals_premium,
        "vals_diesel": vals_diesel,
        "vals_adic_regular": vals_adic_regular,
        "vals_adic_premium": vals_adic_premium,
        "vals_adic_diesel": vals_adic_diesel,
        "vigencias_texto": vigencias_texto,
        "ultima_fecha_tc": ultima_fecha_tc,
        "ultima_vigencia": ultima_vigencia,
    }
    guardar_cache(datos)
    return datos


def construir_respuesta(datos, desde_cache=False):
    grafica_json = generar_grafica_json(datos)

    resultado = {
        "fecha_consulta": datos["fecha_consulta"],
        "tipo_cambio": None,
        "ieps": None,
        "grafica": grafica_json,
        "desde_cache": desde_cache,
        "historico_raw": {
            "fechas_tc": datos["fechas_tc"],
            "valores_tc": datos["valores_tc"],
            "fechas_ieps": datos["fechas_ieps"],
            "vigencias_ieps": datos.get("vigencias_texto", []),
            "vals_regular": datos["vals_regular"],
            "vals_premium": datos["vals_premium"],
            "vals_diesel": datos["vals_diesel"],
            "vals_adic_regular": datos.get("vals_adic_regular", []),
            "vals_adic_premium": datos.get("vals_adic_premium", []),
            "vals_adic_diesel": datos.get("vals_adic_diesel", [])
        }
    }

    if datos["valores_tc"]:
        resultado["tipo_cambio"] = {
            "valor": datos["valores_tc"][-1],
            "fecha": datos["ultima_fecha_tc"],
            "variacion": calcular_variacion(datos["valores_tc"]),
        }

    if datos["vals_regular"]:
        resultado["ieps"] = {
            "vigencia": datos["ultima_vigencia"],
            "regular": {
                "valor": datos["vals_regular"][-1],
                "adicional": datos.get("vals_adic_regular", [0.0])[-1] if datos.get("vals_adic_regular") else 0.0,
                "variacion": calcular_variacion(datos["vals_regular"]),
            },
            "premium": {
                "valor": datos["vals_premium"][-1],
                "adicional": datos.get("vals_adic_premium", [0.0])[-1] if datos.get("vals_adic_premium") else 0.0,
                "variacion": calcular_variacion(datos["vals_premium"]),
            },
            "diesel": {
                "valor": datos["vals_diesel"][-1],
                "adicional": datos.get("vals_adic_diesel", [0.0])[-1] if datos.get("vals_adic_diesel") else 0.0,
                "variacion": calcular_variacion(datos["vals_diesel"]),
            },
        }

    return resultado


# ==========================================
# ENDPOINTS API FastAPI
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def index():
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>DOF Monitor</h1><p>Archivo templates/index.html no encontrado.</p>"


@app.get("/api/consultar")
async def consultar(force: bool = False):
    if not force and cache_valido():
        datos = obtener_cache()
        resultado = await run_in_threadpool(construir_respuesta, datos, True)
        return JSONResponse(content=resultado)

    datos = await run_in_threadpool(run_scraper)
    resultado = await run_in_threadpool(construir_respuesta, datos, False)
    return JSONResponse(content=resultado)
