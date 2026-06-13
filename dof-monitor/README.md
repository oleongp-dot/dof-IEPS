# DOF Monitor 📡

Web app para consultar el tipo de cambio USD/MXN y cuotas IEPS de combustibles publicados en el Diario Oficial de la Federación.

## Correr localmente

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Abre http://localhost:8000

## Deploy en Railway

1. Sube este proyecto a un repo de GitHub
2. Entra a https://railway.app y conecta el repo
3. Railway detecta el `Procfile` automáticamente
4. Dale Deploy — en 2 minutos tienes tu URL

## Deploy en Render

1. Sube a GitHub
2. En Render crea un nuevo "Web Service"
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Stack

- **FastAPI** — backend Python
- **BeautifulSoup** — scraping del DOF
- **Matplotlib** — gráfica histórica
- **Vanilla JS** — frontend sin frameworks
