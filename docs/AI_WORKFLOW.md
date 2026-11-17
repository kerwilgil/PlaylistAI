# Desarrollo asistido por IA: evidencia y límites

Este documento distingue la integración de IA del producto de la asistencia recibida durante su desarrollo. La presencia de una integración no demuestra por sí sola quién escribió el código ni qué herramienta se usó para desarrollarlo.

## Hechos verificables en el repositorio

- PlaylistAI utiliza Spotify y proveedores de IA para proponer, verificar y analizar canciones. Las llamadas y los prompts de producto están en `app.py`; no son un registro de conversaciones de desarrollo.
- `local_ai.py` integra Claude Code y Codex como proveedores de la aplicación, con procesos no interactivos y opciones de aislamiento; `tests/test_local_ai.py` comprueba parte de esa integración mediante simulaciones. Los commits `3b136f3` y `84e6b36` describen la incorporación de ambas suscripciones; esto prueba la funcionalidad incorporada, no la autoría de la implementación.
- En la auditoría local del 17 de noviembre de 2026, Codex examinó el repositorio, ejecutó comprobaciones y aplicó las correcciones locales documentadas en el diff. Esa observación corresponde a esta sesión; el historial previo no acredita por sí mismo otras sesiones de desarrollo asistido.

## Metodología de esta auditoría

Se revisaron código, configuración, pruebas, historial Git accesible, workflow de CI y metadatos públicos de la release. Se buscaron problemas reproducibles, se aplicaron cambios pequeños y se ejecutaron comprobaciones locales. Los resultados y sus límites se entregan en el informe de la auditoría; el diff permite revisar los cambios concretos. No se publicaron cambios ni se ejecutaron operaciones contra cuentas de Spotify.

### Casos prácticos de esta sesión

1. Se contrastó la versión mostrada por la interfaz con `VERSION` y la release, y se corrigió la discrepancia y su prueba.
2. Se comprobó el validador de JavaScript inline: fallaba ante una expresión de Jinja. Se ajustó para analizar el valor numérico que recibe el navegador.
3. Se amplió `.gitignore` para proteger variantes `.env.*`, conservando versionada la plantilla `.env.example`.
4. Se documentaron las limitaciones de pruebas, privacidad y atribución para evitar presentar inferencias como hechos.
5. En la continuación de la auditoría se trasladaron los tokens de Spotify a memoria del proceso, se añadieron pruebas sintéticas del ciclo de sesión y se fijaron versiones y hashes para builds aislados. La conexión real con Spotify sigue pendiente de pruebas con credenciales propias.
6. En una comprobación posterior, las integraciones de código fuente de Codex y Claude Code respondieron a una solicitud mínima. Ambas CLI figuraban disponibles en los dos ejecutables; el flujo de generación dentro del ejecutable sigue sin probarse por falta de autenticación Spotify.

## Declaraciones del desarrollador y evidencia pendiente

Para v1.2.0, Codex implementó y verificó cambios locales en los catálogos de OpenAI y NVIDIA NIM, el selector de modelos y el empaquetado de Windows. El [caso de estudio](AI_CASE_STUDY.md) distingue las pruebas reales de las simuladas.

No se proporcionaron declaraciones específicas del desarrollador sobre herramientas, prompts, sesiones anteriores o contribuciones individuales de IA. Para completar un caso de estudio público hacen falta testimonios autorizados, enlaces a sesiones o cambios atribuibles, fechas, ejemplos de revisión humana y resultados medibles que puedan compartirse sin credenciales ni datos personales. No se incluyen prompts de desarrollo inventados.

## Aprendizajes y propuesta de caso de estudio

Una integración de IA verificable no equivale a evidencia de desarrollo asistido; conviene conservar referencias revisables y registrar también las pruebas fallidas y las limitaciones. Un caso divulgativo podría explicar cómo una aplicación local transforma una idea musical en candidatos, verifica canciones en Spotify y permite revisar resultados, seguido de cómo esta auditoría asistida encontró y validó dos discrepancias de mantenimiento. El relato debe separar las capacidades del producto de las contribuciones documentadas de Codex y evitar métricas o atribuciones sin respaldo.
