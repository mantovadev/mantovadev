#Script che contiene le logiche di Asteroid.
extends Area2D

@onready var sprite = $Sprite2D
@onready var collisionShape = $CollisionShape2D
@onready var animation = $AnimatedSprite2D

var speed: int
var direction_x: float
var rotation_speed: int
var is_golden: bool = false

#Array che contene i percorsi logici alle immagini degli asteroids
var sprites = ["res://sprites/asteroids/1.png", "res://sprites/asteroids/2.png", "res://sprites/asteroids/3.png",
"res://sprites/asteroids/4.png", "res://sprites/asteroids/5.png", "res://sprites/asteroids/6.png", "res://sprites/asteroids/golden.png"]

func _ready() -> void:
	var spriteRandom = randi_range(0, sprites.size()-1)
	 #Associo alla proprietà texture di sprite il path all'immagine
	sprite.texture = load(sprites[spriteRandom])
	#Seleziono randomicamente una posizione fuori schermo 
	var width = get_viewport().get_visible_rect().size[0]
	var xPos = randi_range(0, width)
	var yPos = randi_range(-150, -50)
	#Imposto la position del nodo padre a xPos, yPos
	position = Vector2(xPos, yPos)
	
	#Imposto randomicamente le variabili speed, direction_x e rotation_speed
	#Verranno usate nel _process
	speed = randi_range(120, 300)
	direction_x = randf_range(-1, 1)
	rotation_speed = randi_range(40, 100)
	
	if spriteRandom == sprites.size()-1:
		is_golden = true

#Nel metodo _process non facciamo altro che spostare la posizione
#del nodo padre ed aumentare la sua rotation	
func _process(delta: float) -> void:
	position += Vector2(direction_x, 1.0) * speed * delta
	rotation_degrees += rotation_speed * delta

#--- Segnale da collegare del nodo padre Asteroid ---
#Per rilevare collisione con LASER che è un'Area2D
func _on_area_entered(area: Area2D) -> void:
	area.queue_free()
	destroy()

#--- Segnale da collegare del nodo padre Asteroid ---
#Per rilevare collisione con SHIP che è un CharactedBody2D
func _on_body_entered(body: Node2D) -> void:
	destroy()
	if body.has_method("take_damage"):
		body.take_damage()

#Segnale da collegare del nodo AnimatedSprite2D
#Terminata l’animazione eliminiamo l’oggetto
func _on_animated_sprite_2d_animation_finished() -> void:
	queue_free()

#Metodo che chiama GameManager per incrementare il punteggio
#Successivamente con set_deferred disabilita le collisioni. Non
#e' consigliato fare modifiche sulla fisica degli oggetti durante
#il _process, ma delegare Godot a fare la modifica appena e'
#possibile con set_deferred
func destroy():
	GameManager.increase_score(is_golden)
	sprite.visible = false
	collisionShape.set_deferred("disabled", true)
	speed = 0
	rotation_speed = 0
	animation.play("explosion")
