#Script che contiene le logiche di Laser
extends Area2D

@export var speed: int
@onready var sprite = $Sprite2D

#Vogliamo che il laser si muova solo verso l'alto
var direction = Vector2(0, -1)

func _process(delta: float) -> void:
	position += direction * speed * delta
