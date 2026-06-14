import os
import requests
from bs4 import BeautifulSoup
import warnings
import datetime
import re
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import base64
import io
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

warnings.filterwarnings("ignore")

app = FastAPI()

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

palabras_clave = [
    "tipo de cambio", "ieps", "combustibles",
    "cuotas disminuidas", "estímulo fiscal"
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
            inicio = texto.find("Artículo Tercero") if "Artículo Tercero" in texto else texto.find("ARTÍCULO TERCERO")
            bloque = texto[inicio:inicio+1200]

            vigencia = re.search(r"periodo comprendido del (.+?\d{4})", bloque, re.IGNORECASE)
            vigencia_str = vigencia.group(1).strip() if vigencia else "No disponible"

            patrones = {
                "magna":   r"Gasolina\s+menor\s+a\s+91\s+octanos\s+(\$[\d.]+)",
                "premium": r"Gasolina\s+mayor\s+o\s+igual\s+a\s+91\s+octanos.*?(\$[\d.]+)",
                "diesel":  r"Diésel\s+(\$[\d.]+)"
            }

            valores = {}
            for key, patron in patrones.items():
                match = re.search(patron, bloque, re.DOTALL | re.IGNORECASE)
                if match:
                    valores[key] = float(match.group(1).replace("$", ""))

            if len(valores) == 3:
                return {"fecha": fecha_str, "vigencia": vigencia_str, **valores}
    except:
        pass
    return None


def extraer_tipo_cambio(url, fecha_str):
    try:
        respuesta = requests.get(url, headers=headers, verify=False, timeout=8)
        soup = BeautifulSoup(respuesta.text, "html.parser")
        texto = soup.get_text()

        match = re.search(r"(?:equivalencia|tipo de cambio).*?(\d{2}\.\d{4})", texto, re.IGNORECASE | re.DOTALL)
        if not match:
            match = re.search(r"\b(\d{2}\.\d{4})\b", texto)

        if match:
            valor = float(match.group(1) if len(match.groups()) > 0 else match.group())
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
        return {"texto": f"+${diferencia:.4f}", "tipo": "sube", "valor": diferencia}
    elif diferencia < 0:
        return {"texto": f"-${abs(diferencia):.4f}", "tipo": "baja", "valor": diferencia}
    return {"texto": "Sin cambios", "tipo": "neutral", "valor": 0}


def generar_grafica_base64(datos):
    fechas_tc    = datos["fechas_tc"]
    valores_tc   = datos["valores_tc"]
    fechas_ieps  = datos["fechas_ieps"]
    vals_magna   = datos["vals_magna"]
    vals_premium = datos["vals_premium"]
    vals_diesel  = datos["vals_diesel"]

    if not fechas_tc and not fechas_ieps:
        return None

    def parse_fecha(f):
        return datetime.datetime.strptime(f, "%d/%m/%Y")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), facecolor="#0D1117")
    fig.suptitle("DOF Monitor — Histórico 7 días", fontsize=15, fontweight="bold", color="#E6EDF3", y=0.98)

    for ax in [ax1, ax2]:
        ax.set_facecolor("#161B22")
        ax.tick_params(colors="#8B949E", labelsize=8)
        ax.spines[:].set_color("#30363D")
        ax.grid(True, alpha=0.15, color="#8B949E")

    if fechas_tc:
        fechas_dt = [parse_fecha(f) for f in fechas_tc]
        ax1.plot(fechas_dt, valores_tc, color="#58A6FF", linewidth=2, marker="o", markersize=4)
        ax1.fill_between(fechas_dt, valores_tc, min(valores_tc) - 0.1, alpha=0.08, color="#58A6FF")
        ax1.set_title("💱 Tipo de Cambio USD/MXN", color="#E6EDF3", fontsize=11, pad=8)
        ax1.set_ylabel("Pesos por dólar", color="#8B949E", fontsize=9)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        ax1.tick_params(axis="x", rotation=45)
        if valores_tc:
            ax1.annotate(f"${valores_tc[-1]}", xy=(fechas_dt[-1], valores_tc[-1]),
                        xytext=(-40, 10), textcoords="offset points",
                        fontsize=10, fontweight="bold", color="#58A6FF",
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="#161B22", edgecolor="#58A6FF"))

    if fechas_ieps:
        fechas_dt2 = [parse_fecha(f) for f in fechas_ieps]
        ax2.plot(fechas_dt2, vals_magna,   color="#FF7B72", linewidth=2, marker="o", markersize=4, label="Magna (<91 oct)")
        ax2.plot(fechas_dt2, vals_premium, color="#FFA657", linewidth=2, marker="o", markersize=4, label="Premium (≥91 oct)")
        ax2.plot(fechas_dt2, vals_diesel,  color="#3FB950", linewidth=2, marker="o", markersize=4, label="Diésel")
        ax2.set_title("⛽ IEPS Combustibles (pesos/litro)", color="#E6EDF3", fontsize=11, pad=8)
        ax2.set_ylabel("Pesos por litro", color="#8B949E", fontsize=9)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        ax2.tick_params(axis="x", rotation=45)
        ax2.legend(facecolor="#21262D", edgecolor="#30363D", labelcolor="#E6EDF3", fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=150, facecolor="#0D1117")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


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

    resultados_dias.sort(key=lambda x: datetime.datetime.strptime(x["fecha"], "%d/%m/%Y"))

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

    # Guardamos solo datos simples en caché (sin matplotlib)
    datos = {
        "fecha_consulta": hoy.strftime("%d/%m/%Y %H:%M"),
        "fechas_tc": fechas_tc, "valores_tc": valores_tc,
        "fechas_ieps": fechas_ieps, "vals_magna": vals_magna,
        "vals_premium": vals_premium, "vals_diesel": vals_diesel,
        "ultima_fecha_tc": ultima_fecha_tc,
        "ultima_vigencia": ultima_vigencia
    }
    guardar_cache(datos)
    return datos


def construir_respuesta(datos, desde_cache=False):
    grafica_b64 = generar_grafica_base64(datos)

    resultado = {
        "fecha_consulta": datos["fecha_consulta"],
        "tipo_cambio": None,
        "ieps": None,
        "grafica": grafica_b64,
        "desde_cache": desde_cache
    }

    if datos["valores_tc"]:
        resultado["tipo_cambio"] = {
            "valor": datos["valores_tc"][-1],
            "fecha": datos["ultima_fecha_tc"],
            "variacion": calcular_variacion(datos["valores_tc"])
        }

    if datos["vals_magna"]:
        resultado["ieps"] = {
            "vigencia": datos["ultima_vigencia"],
            "magna":   {"valor": datos["vals_magna"][-1],   "variacion": calcular_variacion(datos["vals_magna"])},
            "premium": {"valor": datos["vals_premium"][-1], "variacion": calcular_variacion(datos["vals_premium"])},
            "diesel":  {"valor": datos["vals_diesel"][-1],  "variacion": calcular_variacion(datos["vals_diesel"])}
        }

    return resultado


# ==========================================
# ENDPOINTS
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def index():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/consultar")
async def consultar(force: bool = False):
    if not force and cache_valido():
        datos = obtener_cache()
        resultado = await run_in_threadpool(construir_respuesta, datos, True)
        return JSONResponse(content=resultado)

    datos = await run_in_threadpool(run_scraper)
    resultado = await run_in_threadpool(construir_respuesta, datos, False)
    return JSONResponse(content=resultado)
