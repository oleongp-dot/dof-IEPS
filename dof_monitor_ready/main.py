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
# CACHÉ — guarda solo datos, no la gráfica
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
# SCRAPING
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
                "magna": r"Gasolina\s+menor\s+a\s+91\s+octanos\s+(\$[\d.]+)",
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
            respuesta = requests.get(
                url, headers=headers, verify=False, timeout=8
            )
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
                        if (
                            "nota_detalle" not in enlace
                            and fecha_str not in enlace
                            and "indicadores" not in enlace
                        ):
                            continue
                        if "tipo de cambio" in texto:
                            tipo_cambio_url = enlace
                        elif ieps_url is None:
                            ieps_url = enlace
                        break
        except:
            pass

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
# GENERACIÓN DE GRÁFICA CON PLOTLY
# ==========================================
def generar_grafica_json(datos):
    fechas_tc = datos["fechas_tc"]
    valores_tc = datos["valores_tc"]
    fechas_ieps = datos["fechas_ieps"]
    vals_magna = datos["vals_magna"]
    vals_premium = datos["vals_premium"]
    vals_diesel = datos["vals_diesel"]

    if not fechas_tc and not fechas_ieps:
        return None

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.15,
        subplot_titles=(
            "💱 Tipo de Cambio USD/MXN",
            "⛽ IEPS Combustibles (pesos/litro)",
        ),
    )

    if fechas_tc:
        fig.add_trace(
            gr.Scatter(
                x=fechas_tc,
                y=valores_tc,
                mode="lines+markers",
                name="USD/MXN",
                line=dict(color="#58A6FF", width=2),
                marker=dict(size=6),
                fill="tozeroy",
                fillcolor="rgba(88, 166, 255, 0.08)",
            ),
            row=1,
            col=1,
        )

    if fechas_ieps:
        fig.add_trace(
            gr.Scatter(
                x=fechas_ieps,
                y=vals_magna,
                mode="lines+markers",
                name="Magna (<91 oct)",
                line=dict(color="#FF7B72", width=2),
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            gr.Scatter(
                x=fechas_ieps,
                y=vals_premium,
                mode="lines+markers",
                name="Premium (≥91 oct)",
                line=dict(color="#FFA657", width=2),
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            gr.Scatter(
                x=fechas_ieps,
                y=vals_diesel,
                mode="lines+markers",
                name="Diésel",
                line=dict(color="#3FB950", width=2),
            ),
            row=2,
            col=1,
        )

    fig.update_layout(
        title={
            "text": "DOF Monitor — Histórico 7 días",
            "y": 0.98,
            "x": 0.5,
            "xanchor": "center",
            "yanchor": "top",
        },
        font=dict(color="#E6EDF3", family="Segoe UI, sans-serif"),
        paper_bgcolor="#0D1117",
        plot_bgcolor="#161B22",
        height=600,
        showlegend=True,
        legend=dict(
            bgcolor="#21262D", bordercolor="#30363D", font=dict(size=10)
        ),
        margin=dict(l=60, r=40, t=80, b=40),
    )

    fig.update_xaxes(
        showgrid=True,
        gridcolor="rgba(139, 148, 158, 0.1)",
        tickfont=dict(size=10, color="#8B949E"),
        linecolor="#30363D",
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(139, 148, 158, 0.1)",
        tickfont=dict(size=10, color="#8B949E"),
        linecolor="#30363D",
    )

    fig.update_yaxes(title_text="Pesos por dólar", row=1, col=1)
    fig.update_yaxes(title_text="Pesos por litro", row=2, col=1)

    return fig.to_json()


def run_scraper():
    hoy = datetime.datetime.now()
    dias = [hoy - datetime.timedelta(days=i) for i in range(7, -1, -1)]

    resultados_dias = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(buscar_dia, fecha): fecha for fecha in dias}
        for future in as_completed(futures):
            resultado = future.result()
            if resultado:
                resultados_dias.append(resultado)

    resultados_dias.sort(
        key=lambda x: datetime.datetime.strptime(x["fecha"], "%d/%m/%Y")
    )

    fechas_tc, valores_tc = [], []
    fechas_ieps, vals_magna, vals_premium, vals_diesel = [], [], [], []
    ultima_fecha_tc = "No disponible"
    ultima_vigencia = "No disponible"

    for dia in resultados_dias:
        if dia["tc"]:
            fechas_tc.append(dia["tc"]["fecha"])
            valores_tc.append(dia["tc"]["valor"])
            ultima_fecha_tc = dia["tc"]["fecha"]
        if dia["ieps"]:
            fechas_ieps.append(dia["ieps"]["fecha"])
            vals_magna.append(dia["ieps"]["magna"])
            vals_premium.append(dia["ieps"]["premium"])
            vals_diesel.append(dia["ieps"]["diesel"])
            ultima_vigencia = dia["ieps"]["vigencia"]

    datos = {
        "fecha_consulta": hoy.strftime("%d/%m/%Y %H:%M"),
        "fechas_tc": fechas_tc,
        "valores_tc": valores_tc,
        "fechas_ieps": fechas_ieps,
        "vals_magna": vals_magna,
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
    }

    if datos["valores_tc"]:
        resultado["tipo_cambio"] = {
            "valor": datos["valores_tc"][-1],
            "fecha": datos["ultima_fecha_tc"],
            "variacion": calcular_variacion(datos["valores_tc"]),
        }

    if datos["vals_magna"]:
        resultado["ieps"] = {
            "vigencia": datos["ultima_vigencia"],
            "magna": {
                "valor": datos["vals_magna"][-1],
                "variacion": calcular_variacion(datos["vals_magna"]),
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
# ENDPOINTS
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
