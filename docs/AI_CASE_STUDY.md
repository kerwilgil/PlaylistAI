# PlaylistAI: integración de IA y auditoría asistida

## El producto y las fuentes de evidencia

PlaylistAI recibe una idea musical, solicita candidatos a un proveedor de IA y los contrasta con el catálogo de Spotify antes de crear o ampliar una lista. El proyecto también analiza listas existentes. `app.py` implementa la coordinación y `local_ai.py` integra llamadas no interactivas a Claude Code y Codex como proveedores alternativos. Las pruebas de `tests/` verifican partes del comportamiento con respuestas simuladas. Esta integración es una característica del producto y **no demuestra la autoría de su código**.

En la auditoría iniciada el 17 de noviembre de 2026, Codex examinó el repositorio y sus cambios locales, ejecutó pruebas e hizo correcciones revisables en el diff. La evidencia disponible es esta sesión, el código y los resultados descritos aquí; no se conserva aquí una transcripción independiente de la sesión. Los commits `3b136f3` y `84e6b36` describen la incorporación de proveedores locales, pero no prueban que Codex o Claude desarrollaran esas versiones. No se han aportado testimonios, prompts de desarrollo ni sesiones históricas verificables. No se les atribuye trabajo histórico.

## Problemas observados y trabajo realizado

- La interfaz indicaba 1.1.0, mientras `VERSION` y la release indicaban 1.1.1. Se corrigieron el valor y su prueba.
- El comprobador de JavaScript fallaba al analizar directamente una expresión de Jinja. Ahora sustituye ese valor numérico antes de comprobar la sintaxis.
- `.gitignore` no cubría variantes como `.env.production`; ahora las ignora sin excluir `.env.example`.
- Los README enlazaban a `CONTEXT.md`, ausente, y describían de forma incompleta dónde reside el token. Se corrigieron ambos puntos.
- Se añadió una prueba local simulada para `state`, intercambio de código, renovación y cierre de sesión. No sustituye una conexión real con Spotify.

La base `origin/main` pasó 76 pruebas en Python 3.12; el árbol corregido pasó 84 en Python 3.11 y 3.12, incluidas pruebas OAuth simuladas. El comprobador JavaScript fallaba en la base y pasó con el cambio. El build Windows aislado arrancó y respondió HTTP 200; `/api/me` devolvió 401 sin sesión. También se comprobó el arranque de la release publicada bajo las mismas condiciones. Ruff pasó en los archivos nuevos; el análisis global señaló 32 observaciones, frente a 33 en la base. Estas comprobaciones no demuestran ausencia de regresiones en flujos no ejecutados.

En una comprobación posterior, ambos ejecutables se iniciaron con configuración temporal vacía: la página respondió 200 y los cinco endpoints de Spotify comprobados respondieron 401 sin sesión. Ambos detectaron las CLI de Codex y Claude Code; no detectaron claves API de IA en ese entorno. Una llamada mínima a cada integración `local_ai.py` desde el código fuente recibió la respuesta solicitada. Esto no valida la generación desde la interfaz del ejecutable ni la conexión real con Spotify. La revisión de las 32 observaciones de Ruff no confirmó un error funcional que justificara cambiar código.

## Sesión de Spotify

Antes del cambio, el callback guardaba `token_info` en la cookie firmada pero no cifrada de Flask. Una prueba sintética confirmó que podía recuperarse sin conocer la clave de firma.

Ahora los tokens viven en memoria del proceso, indexados por un identificador aleatorio en la cookie `HttpOnly` y `SameSite=Lax`. Los estados OAuth son de un solo uso y caducan a los 10 minutos; las sesiones caducan tras 12 horas sin uso y el almacén está limitado a 128 entradas. Un bloqueo global coordina renovación y logout; una renovación puede retrasar otras sesiones y está sujeta a un timeout HTTP de 12 segundos por operación de conexión o lectura. Las pruebas sintéticas cubren replay, expiración, fallos, concurrencia y un nuevo proceso. Logout invalida la sesión local, pero no revoca el token ante Spotify ni interrumpe necesariamente una operación ya en curso. Reiniciar requiere reconectar; varios procesos necesitarían un almacén compartido. La cookie sigue siendo una credencial de sesión reutilizable hasta su invalidación, y el malware con acceso al proceso no queda fuera de alcance. La validación contra Spotify real sigue pendiente.

## Reproducibilidad de dependencias

Los scripts de distribución usan Python 3.12.15, un entorno temporal aislado y `requirements-build.lock`, con versiones exactas y hashes verificados por `uv pip install --require-hashes --only-binary :all:`. Se conservaron las versiones principales ya probadas de Flask 3.1.3, Spotipy 2.26.0, Requests 2.34.2 y PyInstaller 6.21.0. La resolución para macOS Intel y Apple Silicon se comprobó en modo simulado; el build macOS real queda pendiente. El lock no garantiza binarios idénticos byte a byte. Los lanzadores de código fuente y CI todavía usan `requirements.txt`, con rangos; no se presentan como instalaciones fijadas.

## Procedimiento de validación con Spotify y Windows

Requiere una cuenta y credenciales propias, una app de Spotify configurada para `http://127.0.0.1:5000/callback`, y un proveedor de IA disponible. No compartas el contenido de `.env`, cookies, tokens ni capturas que los muestren. Usa una playlist privada de prueba y elimina manualmente los datos de prueba cuando termines.

1. Ejecuta `python -m pytest -q` en un entorno con las dependencias del proyecto. En Windows, construye con `powershell -File scripts/build_windows.ps1` y ejecuta `dist/windows/PlaylistAI.exe`. El script limpia el directorio `build` y algunas salidas antiguas de `dist`; preserva primero cualquier archivo propio allí. El primer arranque crea la configuración; rellénala localmente y vuelve a iniciar.
2. Abre `http://127.0.0.1:5000`, conecta Spotify y acepta los permisos. Comprueba que la cuenta mostrada es la esperada. Rechaza una autorización y confirma que la app muestra el error.
3. Con un proveedor de IA configurado, crea una playlist privada pequeña y verifica en Spotify nombre, canciones y que no se han añadido pistas inesperadas. Prueba analizarla y agregar una canción; revisa el resultado antes de cualquier eliminación.
4. Cierra sesión, verifica que la interfaz pide reconectar y vuelve a autenticar. Comprueba una renovación tras la expiración del access token, sin registrar valores de tokens. Anota plataforma, versiones, fecha, pasos, resultados y errores redactados.
5. Repite con el ejecutable de distribución propuesto. El arranque HTTP sin credenciales ya se comprobó; la autenticación, renovación y operaciones reales siguen pendientes de esta prueba.

Para cada proveedor de IA que se quiera validar, selecciona el modo y modelo disponibles en Configuración, pide una lista privada de cinco canciones con una descripción no sensible y comprueba que aparecen resultados verificables en Spotify. Repite por separado con las API configuradas (Anthropic, OpenAI o NVIDIA) y con cada CLI local que esté instalada y autenticada (Claude Code o Codex). Registra proveedor, modelo, versión de CLI, fecha, éxito o error redactado y canciones aceptadas; no registres prompts privados, claves ni respuestas completas. Las llamadas pueden consumir cuota o créditos. Si un proveedor no está configurado, marca su fila «NO PROBADO», no «FAIL».

## Comprobación de distribución

El ZIP Windows de `v1.1.1` coincidió con el SHA-256 publicado en los metadatos de GitHub y contiene `PlaylistAI.exe` y `.env.example`. El ejecutable publicado contiene 4.477 entradas PyInstaller frente a 72 en el build aislado anterior y 88 en el nuevo build fijado: incluye 3.660 recursos de Django y módulos de OpenAI y Anthropic, que no aparecen en los builds aislados. El código de la app invoca las API por HTTP, por lo que la diferencia es compatible con dependencias adicionales presentes durante el empaquetado; no se ha establecido la causa exacta. El entorno aislado evita depender de paquetes ajenos sin excluir módulos manualmente. El hash no prueba autoría ni reproducibilidad. Gitleaks 8.30.1 no detectó secretos en el historial Git accesible, el árbol de trabajo ni las cadenas y recursos textuales extraídos de ambos ejecutables anteriores; no cubre exhaustivamente binarios ni imágenes.

## Aprendizajes y límites públicos

En la preparación de v1.2.0 se comprobaron los catálogos reales de OpenAI y NVIDIA NIM y se generó JSON de playlist con NVIDIA. El selector se probó en Windows con un catálogo simulado, incluyendo teclado y tamaños de 375 y 1280 px. La creación de una playlist real en Spotify y la persistencia desde la interfaz autenticada del ejecutable quedaron pendientes de autorización interactiva.

La verificación humana y las pruebas determinan qué puede afirmarse. La auditoría ilustra cómo Codex puede ayudar a contrastar documentación, código, pruebas y artefactos; no demuestra que todas las funciones estén libres de defectos ni atribuye desarrollos anteriores. Antes de presentar un caso más amplio, añadir evidencia autorizada de sesiones históricas, resultados de la prueba real con Spotify y revisión del entorno de build de la release.
