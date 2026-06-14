# DOF Monitor

Monitoreo automatizado del Diario Oficial de la Federación (DOF) para capturar el Tipo de Cambio USD/MXN y las cuotas de estímulos fiscales del IEPS para combustibles (Magna, Premium y Diésel).

## Cambios Recientes
- Se migró de `matplotlib` a `plotly` para evitar bloqueos del `run_in_threadpool` y habilitar gráficas interactivas nativas.
- Optimizado para despliegues rápidos en Render y Railway.
