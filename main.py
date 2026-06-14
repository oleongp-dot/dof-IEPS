def generar_grafica_json(datos):
    fechas_tc = datos.get("fechas_tc", [])
    valores_tc = datos.get("valores_tc", [])
    fechas_ieps = datos.get("fechas_ieps", [])
    vals_magna = datos.get("vals_magna", [])
    vals_premium = datos.get("vals_premium", [])
    vals_diesel = datos.get("vals_diesel", [])

    # Creamos listas limpias ordenadas cronológicamente para evitar superposiciones
    puntos_tc = []
    for f, v in zip(fechas_tc, valores_tc):
        try:
            f_date = datetime.datetime.strptime(f, "%d/%m/%Y")
            puntos_tc.append((f_date, v))
        except:
            continue
    puntos_tc.sort(key=lambda x: x[0])

    puntos_ieps = []
    for f, m, p, d in zip(fechas_ieps, vals_magna, vals_premium, vals_diesel):
        try:
            f_date = datetime.datetime.strptime(f, "%d/%m/%Y")
            puntos_ieps.append((f_date, m, p, d))
        except:
            continue
    puntos_ieps.sort(key=lambda x: x[0])

    if not puntos_tc and not puntos_ieps:
        return None

    # Inicializamos los subplots de Plotly
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=False,
        vertical_spacing=0.15,
        subplot_titles=("💱 Tipo de Cambio USD/MXN", "⛽ IEPS Combustibles (pesos/litro)")
    )

    # 1. Trazar Gráfica de Tipo de Cambio
    if puntos_tc:
        fx_tc = [p[0].strftime("%Y-%m-%d") for p in puntos_tc]
        vy_tc = [p[1] for p in puntos_tc]
        
        fig.add_trace(
            gr.Scatter(
                x=fx_tc, y=vy_tc,
                mode='lines+markers',
                name='USD/MXN',
                line=dict(color='#58A6FF', width=2.5),
                marker=dict(size=7, symbol='circle'),
                fill='tozeroy',
                fillcolor='rgba(88, 166, 255, 0.06)'
            ),
            row=1, col=1
        )

    # 2. Trazar Gráfica de IEPS Combustibles
    if puntos_ieps:
        fx_ieps = [p[0].strftime("%Y-%m-%d") for p in puntos_ieps]
        vy_magna = [p[1] for p in puntos_ieps]
        vy_premium = [p[2] for p in puntos_ieps]
        vy_diesel = [p[3] for p in puntos_ieps]
        
        fig.add_trace(
            gr.Scatter(x=fx_ieps, y=vy_magna, mode='lines+markers', name='Magna (<91 oct)', line=dict(color='#FF7B72', width=2.5), marker=dict(size=7)),
            row=2, col=1
        )
        fig.add_trace(
            gr.Scatter(x=fx_ieps, y=vy_premium, mode='lines+markers', name='Premium (≥91 oct)', line=dict(color='#FFA657', width=2.5), marker=dict(size=7)),
            row=2, col=1
        )
        fig.add_trace(
            gr.Scatter(x=fx_ieps, y=vy_diesel, mode='lines+markers', name='Diésel', line=dict(color='#3FB950', width=2.5), marker=dict(size=7)),
            row=2, col=1
        )

    # Configuración de estilos generales (Dark Mode elegante de GitHub)
    fig.update_layout(
        font=dict(color="#E6EDF3", family="Segoe UI, sans-serif"),
        paper_bgcolor="#0D1117",
        plot_bgcolor="#161B22",
        height=600,
        showlegend=True,
        legend=dict(bgcolor="#21262D", bordercolor="#30363D", font=dict(size=10)),
        margin=dict(l=60, r=40, t=50, b=50)
    )

    # Ajustes estrictos de los ejes x para evitar distorsiones temporales
    fig.update_xaxes(
        type='category',  # Forzamos modo categoría para que se distribuya homogéneo día a día
        showgrid=True,
        gridcolor='rgba(139, 148, 158, 0.08)',
        tickfont=dict(size=10, color="#8B949E"),
        linecolor="#30363D"
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor='rgba(139, 148, 158, 0.08)',
        tickfont=dict(size=10, color="#8B949E"),
        linecolor="#30363D"
    )

    fig.update_yaxes(title_text="Pesos por dólar", autofocus=True, row=1, col=1)
    fig.update_yaxes(title_text="Pesos por litro", row=2, col=1)

    return fig.to_json()
