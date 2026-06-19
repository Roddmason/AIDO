"""Paquete del slice de seguridad: clasificacion, politica, grants y sandbox de ejecucion.

Reune el motor de politicas, el clasificador de riesgo, los grants de aprobacion y los
sandboxes que aislan la ejecucion. Es solo agregador: no exporta simbolos ni define
invariantes propias; cada submodulo declara que permite/deniega y que excepcion lanza.
"""

__all__ = []
