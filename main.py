def buscar_dia(fecha):
    day = fecha.strftime("%d")
    month = fecha.strftime("%m")
    year = fecha.strftime("%Y")
    fecha_str = f"{day}/{month}/{year}"

    # Si es fin de semana, el DOF no publica de forma ordinaria indicadores fiscales o de tipo de cambio
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

    # Si no se encontró absolutamente nada este día hábil, lo descartamos para no ensuciar la gráfica
    if not tipo_cambio_url and not ieps_url:
        return None

    resultado_dia = {"fecha": fecha_str, "tc": None, "ieps": None}
    if tipo_cambio_url:
        resultado_dia["tc"] = extraer_tipo_cambio(tipo_cambio_url, fecha_str)
    if ieps_url:
        resultado_dia["ieps"] = extraer_ieps(ieps_url, fecha_str)

    return resultado_dia


def run_scraper():
    hoy = datetime.datetime.now()
    resultados_dias = []
    
    # Buscaremos en una ventana de hasta 12 días hacia atrás para recolectar al menos 5 días hábiles con publicaciones reales
    dias_a_revisar = [hoy - datetime.timedelta(days=i) for i in range(12)]
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(buscar_dia, fecha): fecha for fecha in dias_a_revisar}
        for future in as_completed(futures):
            try:
                resultado = future.result(timeout=10)
                # Almacenamos solo si el día arrojó información válida de TC o IEPS
                if resultado and (resultado.get("tc") or resultado.get("ieps")):
                    resultados_dias.append(resultado)
            except Exception:
                pass

    # Forzar el ordenamiento estrictamente cronológico por la fecha del objeto
    try:
        resultados_dias.sort(key=lambda x: datetime.datetime.strptime(x["fecha"], "%d/%m/%Y"))
    except:
        pass

    fechas_tc, valores_tc = [], []
    fechas_ieps, vals_magna, vals_premium, vals_diesel = [], [], [], []
    ultima_fecha_tc = "No disponible"
    ultima_vigencia = "No disponible"

    for dia in resultados_dias:
        if dia.get("tc") and dia["tc"].get("valor"):
            fechas_tc.append(dia["tc"]["fecha"])
            valores_tc.append(dia["tc"]["valor"])
            ultima_fecha_tc = dia["tc"]["fecha"]
            
        if dia.get("ieps") and dia["ieps"].get("magna"):
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
