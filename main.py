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

palabras_clave = [
    "tipo de cambio",
    "ieps",
    "combustibles",
    "cuotas disminuidas",
    "estímulo fiscal",
]

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
# SCRAPERS DE EXTRACCIÓN
# ==========================================
def extraer_ieps(url, fecha_str):
    try:
        respuesta = requests.get(url, headers=headers, verify=False, timeout=8)
        soup = BeautifulSoup(respuesta.text, "html.parser")
        texto = soup.get_text()

        if "Artículo Tercero" in texto or "ARTÍCULO TERCERO" in texto:
            inicio = (
                texto.find("Artículo Tercero")
                if "Artículo Tercero" in texto
                else texto.find("ARTÍCULO TERCERO")
            )
            bloque = texto[inicio : inicio + 1200]

            vigencia = re.search(
                r"periodo comprendido del (.+?\d{4})", bloque, re.IGNORECASE
            )
            vigencia_str = (
                vigencia.group(1).strip() if vigencia else "No disponible"
            )

            patrones = {
                "regular": r"Gasolina\s+menor\s+a\s+91\s+octanos\s+(\$[\d.]+)",
                "premium": r"Gasolina\s+mayor\s+o\s+igual\s+a\s+91\s+octanos.*?(\$[\d.]+)",
                "diesel": r"Diésel\s+(\$[\d.]+)",
            }

            valores = {}
            for key, patron in patrones.items():
                match = re.search(patron, bloque, re.DOTALL | re.IGNORECASE)
                if match:
                    valores[key] = float(match.group(1).replace("$", ""))

            if len(valores) == 3:
                return {
                    "fecha": fecha_str,
                    "vigencia": vigencia_str,
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
                if not texto:
                    continue
                for palabra in palabras_clave:
                    if palabra in texto:
                        enlace = pub.get("href", "")
                        if enlace and not enlace.startswith("http"):
                            enlace = f"https://dof.gob.mx/{enlace}"
                        if "nota_detalle" not in enlace and fecha_str not in enlace and "indicadores" not in enlace:
                            continue
                        if "tipo de cambio" in texto:
                            tipo_cambio_url = enlace
                        elif ieps_url is None:
                            ieps_url = enlace
                        break
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

    puntos_tc = []
    for f, v in zip(fechas_tc, valores_tc):
        try:
            f_date = datetime.datetime.strptime(f, "%d/%m/%Y")
            puntos_tc.append((f_date, v))
        except:
            continue
    puntos_tc.sort(key=lambda x: x[0])

    puntos_ieps = []
    for f, m, p, d in zip(fechas_ieps, vals_regular, vals_premium, vals_diesel):
        try:
            f_date = datetime.datetime.strptime(f, "%d/%m/%Y")
            puntos_ieps.append((f_date, m, p, d))
        except:
            continue
    puntos_ieps.sort(key=lambda x: x[0])

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
        vy_regular = [p[1] for p in puntos_ieps]
        vy_premium = [p[2] for p in puntos_ieps]
        vy_diesel = [p[3] for p in puntos_ieps]
        
        fig.add_trace(
            gr.Scatter(x=fx_ieps, y=vy_regular, mode='lines+markers', name='Regular (<91 oct)', line=dict(color='#3FB950', width=2.5), marker=dict(size=6)),
            row=2, col=1
        )
        fig.add_trace(
            gr.Scatter(x=fx_ieps, y=vy_premium, mode='lines+markers', name='Premium (≥91 oct)', line=dict(color='#FF7B72', width=2.5), marker=dict(size=6)),
            row=2, col=1
        )
        fig.add_trace(
            gr.Scatter(x=fx_ieps, y=vy_diesel, mode='lines+markers', name='Diésel', line=dict(color='#FFFFFF', width=2.5), marker=dict(size=6)),
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
            todos_ieps.extend([p[1], p[2], p[3]])
        min_ieps = min(todos_ieps)
        max_ieps = max(todos_ieps)
        margen_ieps = max((max_ieps - min_ieps) * 0.05, 0.05)
        
        fig.update_yaxes(
            title_text="Pesos por litro",
            row=2, col=1,
            autorange=False,
            range=[min_ieps - margen_ieps, max_ieps + margen_ieps],
            showgrid=True,
            gridcolor='rgba(139, 148, 158, 0.08)',
            tickfont=dict(size=10, color="#8B949E"),
            linecolor="#30363D",
            tickformat=".4f"
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
    fechas_ieps, vals_regular, vals_premium, vals_diesel = [], [], [], []
    ultima_fecha_tc = "No disponible"
    ultima_vigencia = "No disponible"

    for dia in resultados_dias:
        if dia.get("tc") and dia["tc"].get("valor"):
            fechas_tc.append(dia["tc"]["fecha"])
            valores_tc.append(dia["tc"]["valor"])
            ultima_fecha_tc = dia["tc"]["fecha"]
            
        if dia.get("ieps") and dia["ieps"].get("regular"):
            fechas_ieps.append(dia["ieps"]["fecha"])
            vals_regular.append(dia["ieps"]["regular"])
            vals_premium.append(dia["ieps"]["premium"])
            vals_diesel.append(dia["ieps"]["diesel"])
            ultima_vigencia = dia["ieps"]["vigencia"]

    datos = {
        "fecha_consulta": hoy.strftime("%d/%m/%Y %H:%M"),
        "fechas_tc": fechas_tc,
        "valores_tc": valores_tc,
        "fechas_ieps": fechas_ieps,
        "vals_regular": vals_regular,
        "vals_premium": vals_premium,
        "vals_diesel": vals_diesel,
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
        # INYECTAMOS EL HISTÓRICO CRUDO PARA LA DESCARGA CSV EN CLIENTE
        "historico_raw": {
            "fechas_tc": datos["fechas_tc"],
            "valores_tc": datos["valores_tc"],
            "fechas_ieps": datos["fechas_ieps"],
            "vals_regular": datos["vals_regular"],
            "vals_premium": datos["vals_premium"],
            "vals_diesel": datos["vals_diesel"]
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
                "variacion": calcular_variacion(datos["vals_regular"]),
            },
            "premium": {
                "valor": datos["vals_premium"][-1],
                "variacion": calcular_variacion(datos["vals_premium"]),
            },
            "diesel": {
                "valor": datos["vals_diesel"][-1],
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
