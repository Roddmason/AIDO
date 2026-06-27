"""Control plane del backend: bootstrap del runtime y ensamblado del overview agregado.

Agrupa las piezas que el proceso FastAPI consume al arrancar: el runtime que posee la
conexion SQLite y el proyecto runtime, el agregador read-only que compone el snapshot
global del estado, y el contrato Pydantic de esa respuesta. No contiene logica de dominio
propia; orquesta a los repositorios de cada slice.

@author Rodrigo Mason
"""
