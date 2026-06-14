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
import json

warnings.filterwarnings("ignore")

app = FastAPI()

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

palabras_clave = [
    "tipo de cambio", "ieps", "combustibles",
    "cuotas disminuidas", "estímulo fiscal"
]


def extraer_ieps(url, fecha_str, data):
    try:
        respuesta = requests.get(url, headers=headers, verify=False, timeout=20)
        soup = BeautifulSoup(respuesta.text, "html.parser")
        texto = soup.get_text()

        if "Artículo Tercero" in texto or "ARTÍCULO TERCERO" in texto:
            inicio = texto.find("Artículo Tercero") if "Artículo Tercero" in texto else texto.find("ARTÍCULO TERCERO")
            bloque = texto[inicio:inicio+1200]

            vigencia = re.search(r"periodo comprendido del (.+?\d{4})", bloque, re.IGNORECASE)
            if vigencia:
                data["ultima_vigencia_ieps"] = vigencia.group(1).strip()

            patrones = {
                "Gasolina < 91 oct": r"Gasolina\s+menor\s+a\s+91\s+octanos\s+(\$[\d.]+)",
                "Gasolina >= 91 oct": r"Gasolina\s+mayor\s+o\s+igual\s+a\s+91\s+octanos.*?(\$[\d.]+)",
                "Diésel": r"Diésel\s+(\$[\d.]+)"
            }

            valores = {}
            for combustible, patron in patrones.items():
                match = re.search(patron, bloque, re.DOTALL | re.IGNORECASE)
                if match:
                    valor = float(match.group(1).replace("$", ""))
                    valores[combustible] = valor

            if len(valores) == 3:
                data["fechas_ieps"].append(fecha_str)
                data["valores_gasolina_menor"].append(valores["Gasolina < 91 oct"])
                data["valores_gasolina_mayor"].append(valores["Gasolina >= 91 oct"])
                data["valores_diesel"].append(valores["Diésel"])
    except:
        pass


def extraer_tipo_cambio(url, fecha_str, data):
    try:
        respuesta = requests.get(url, headers=headers, verify=False, timeout=20)
        soup = BeautifulSoup(respuesta.text, "html.parser")
        texto = soup.get_text()

        match = re.search(r"(?:equivalencia|tipo de cambio).*?(\d{2}\.\d{4})", texto, re.IGNORECASE | re.DOTALL)
        if not match:
            match = re.search(r"\b(\d{2}\.\d{4})\b", texto)

        if match:
            valor = float(match.group(1) if len(match.groups()) > 0 else match.group())
            data["fechas_tc"].append(fecha_str)
            data["valores_tc"].append(valor)
            data["ultima_fecha_tc"] = fecha_str
    except:
        pass


def buscar_dia(fecha, data):
    day = fecha.strftime("%d")
    month = fecha.strftime("%m")
    year = fecha.strftime("%Y")
    fecha_str = f"{day}/{month}/{year}"

    if fecha.weekday() >= 5:
        return

    tipo_cambio_url = None

    for edicion in ["MAT", "VES"]:
        url = f"https://dof.gob.mx/index.php?year={year}&month={month}&day={day}&edicion={edicion}"
        try:
            respuesta = requests.get(url, headers=headers, verify=False, timeout=20)
            soup = BeautifulSoup(respuesta.text, "html.parser")
            publicaciones = soup.find_all("a")

            for pub in publicaciones:
                texto = pub.text.lower().strip()
                if not texto:
                    continue
                for palabra in palabras_clave:
                    if palabra in texto:
                        enlace = pub.get("href", "")
                        if enlace and not enlace.startswith("http"):
                            enlace = f"https://dof.gob.mx/{enlace}"
                        if "nota_detalle" not in enlace and f"{day}/{month}/{year}" not in enlace and "indicadores" not in enlace:
                            continue
                        if "tipo de cambio" in texto:
                            tipo_cambio_url = enlace
                        else:
                            extraer_ieps(enlace, fecha_str, data)
                        break
        except:
            pass

    if tipo_cambio_url:
        extraer_tipo_cambio(tipo_cambio_url, fecha_str, data)


def calcular_variacion(lista_valores):
    if len(lista_valores) < 2:
        return {"texto": "Sin histórico", "tipo": "neutral", "valor": 0}
    actual = lista_valores[-1]
    anterior = lista_valores[-2]
    diferencia = actual - anterior
    if diferencia > 0:
        return {"texto": f"+${diferencia:.4f}", "tipo": "sube", "valor": diferencia}
    elif diferencia < 0:
        return {"texto": f"-${abs(diferencia):.4f}", "tipo": "baja", "valor": diferencia}
    else:
        return {"texto": "Sin cambios", "tipo": "neutral", "valor": 0}


def generar_grafica_base64(data):
    fechas_tc = data["fechas_tc"]
    valores_tc = data["valores_tc"]
    fechas_ieps = data["fechas_ieps"]
    valores_gasolina_menor = data["valores_gasolina_menor"]
    valores_gasolina_mayor = data["valores_gasolina_mayor"]
    valores_diesel = data["valores_diesel"]

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
        ax2.plot(fechas_dt2, valores_gasolina_menor, color="#FF7B72", linewidth=2, marker="o", markersize=4, label="Magna (<91 oct)")
        ax2.plot(fechas_dt2, valores_gasolina_mayor, color="#FFA657", linewidth=2, marker="o", markersize=4, label="Premium (≥91 oct)")
        ax2.plot(fechas_dt2, valores_diesel, color="#3FB950", linewidth=2, marker="o", markersize=4, label="Diésel")
        ax2.set_title("⛽ IEPS Combustibles (pesos/litro)", color="#E6EDF3", fontsize=11, pad=8)
        ax2.set_ylabel("Pesos por litro", color="#8B949E", fontsize=9)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        ax2.tick_params(axis="x", rotation=45)
        legend = ax2.legend(facecolor="#21262D", edgecolor="#30363D", labelcolor="#E6EDF3", fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=150, facecolor="#0D1117")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def run_scraper():
    data = {
        "fechas_tc": [], "valores_tc": [],
        "fechas_ieps": [], "valores_gasolina_menor": [],
        "valores_gasolina_mayor": [], "valores_diesel": [],
        "ultima_fecha_tc": "No disponible",
        "ultima_vigencia_ieps": "No disponible"
    }

    hoy = datetime.datetime.now()
    for i in range(7, -1, -1):
        fecha = hoy - datetime.timedelta(days=i)
        buscar_dia(fecha, data)
        time.sleep(0.3)

    grafica_b64 = generar_grafica_base64(data)

    resultado = {
        "fecha_consulta": hoy.strftime("%d/%m/%Y %H:%M"),
        "tipo_cambio": None,
        "ieps": None,
        "grafica": grafica_b64
    }

    if data["valores_tc"]:
        var = calcular_variacion(data["valores_tc"])
        resultado["tipo_cambio"] = {
            "valor": data["valores_tc"][-1],
            "fecha": data["ultima_fecha_tc"],
            "variacion": var
        }

    if data["valores_gasolina_menor"]:
        resultado["ieps"] = {
            "vigencia": data["ultima_vigencia_ieps"],
            "magna": {
                "valor": data["valores_gasolina_menor"][-1],
                "variacion": calcular_variacion(data["valores_gasolina_menor"])
            },
            "premium": {
                "valor": data["valores_gasolina_mayor"][-1],
                "variacion": calcular_variacion(data["valores_gasolina_mayor"])
            },
            "diesel": {
                "valor": data["valores_diesel"][-1],
                "variacion": calcular_variacion(data["valores_diesel"])
            }
        }

    return resultado


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


def scraper_sync():
    return run_scraper()

@app.get("/api/consultar")
async def consultar():
    resultado = await run_in_threadpool(scraper_sync)
    return JSONResponse(content=resultado)
