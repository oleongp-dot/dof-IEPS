def generar_grafica_json(datos):
    fechas_tc = datos.get("fechas_tc", [])
    valores_tc = datos.get("valores_tc", [])
    fechas_ieps = datos.get("fechas_ieps", [])
    vals_magna = datos.get("vals_magna", [])
    vals_premium = datos.get("vals_premium", [])
    vals_diesel = datos.get("vals_diesel", [])

    if not fechas_tc and not fechas_ieps:
        return None

    # Función interna para convertir "DD/MM/YYYY" a "YYYY-MM-DD" que Plotly entiende de forma nativa
    def formatear_fecha(f_str):
        try:
            return datetime.datetime.strptime(f_str, "%d/%m/%Y").strftime("%Y-%m-%d")
        except:
            return f_str

    # Convertimos los arreglos de fechas
    fechas_tc_clean = [formatear_fecha(f) for f in fechas_tc]
    fechas_ieps_clean = [formatear_fecha(f) for f in fechas_ieps]

    # Creamos los subplots
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=False,
        vertical_spacing=0.15,
        subplot_titles=("💱 Tipo de Cambio USD/MXN", "⛽ IEPS Combustibles (pesos/litro)")
    )

    # 1. Gráfica de Tipo de Cambio
    if fechas_tc_clean and valores_tc:
        # Asegurar orden cronológico emparejado
        pares_tc = sorted(zip(fechas_tc_clean, valores_tc))
        fx, vy = zip(*pares_tc) if pares_tc else ([], [])
        
        fig.add_trace(
            gr.Scatter(
                x=list(fx), y=list(vy),
                mode='lines+markers',
                name='USD/MXN',
                line=dict(color='#58A6FF', width=2),
                marker=dict(size=6),
                fill='tozeroy',
                fillcolor='rgba(88, 166, 255, 0.08)'
            ),
            row=1, col=1
        )

    # 2. Gráficas de IEPS
    if fechas_ieps_clean and vals_magna:
        # Emparejar y ordenar cronológicamente para evitar líneas cruzadas
        pares_ieps = sorted(zip(fechas_ieps_clean, vals_magna, vals_premium, vals_diesel))
        if pares_ieps:
            fx_i, v_m, v_p, v_d = zip(*pares_ieps)
            
            fig.add_trace(
                gr.Scatter(x=list(fx_i), y=list(v_m), mode='lines+markers', name='Magna (<91 oct)', line=dict(color='#FF7B72', width=2)),
                row=2, col=1
            )
            fig.add_trace(
                gr.Scatter(x=list(fx_i), y=list(v_p), mode='lines+markers', name='Premium (≥91 oct)', line=dict(color='#FFA657', width=2)),
                row=2, col=1
            )
            fig.add_trace(
                gr.Scatter(x=list(fx_i), y=list(v_d), mode='lines+markers', name='Diésel', line=dict(color='#3FB950', width=2)),
                row=2, col=1
            )

    # Configuración estética del Layout (Tema Oscuro GitHub)
    fig.update_layout(
        font=dict(color="#E6EDF3", family="Segoe UI, sans-serif"),
        paper_bgcolor="#0D1117",
        plot_bgcolor="#161B22",
        height=600,
        showlegend=True,
        legend=dict(bgcolor="#21262D", bordercolor="#30363D", font=dict(size=10)),
        margin=dict(l=60, r=40, t=50, b=50)
    )

    # Forzar que el eje X ordene cronológicamente y no por strings
    fig.update_xaxes(
        type='date',
        showgrid=True,
        gridcolor='rgba(139, 148, 158, 0.1)',
        tickfont=dict(size=10, color="#8B949E"),
        linecolor="#30363D"
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor='rgba(139, 148, 158, 0.1)',
        tickfont=dict(size=10, color="#8B949E"),
        linecolor="#30363D"
    )

    fig.update_yaxes(title_text="Pesos por dólar", row=1, col=1)
    fig.update_yaxes(title_text="Pesos por litro", row=2, col=1)

    return fig.to_json()
