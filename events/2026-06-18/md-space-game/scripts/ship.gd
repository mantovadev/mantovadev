#Script che contiene le logiche di Ship
extends CharacterBody2D

@export var speed = 400

@onready var fire_rate_timer = $Timer
var laser = preload("res://scenes/laser.tscn")
var can_shoot = true
var is_immune = false

#Ricava il vettore con le direzioni premute rispetto ai tasti e moltiplica 
#per la velocià configurata
func get_input():
	var input_direction = Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
	#velocity è una variabile ereditata da CharacterBody2D e possiamo usarla 
		 #senza dichiararla
	velocity = input_direction * speed

#Nel ciclo principale calcolo velocity nella get_input e poi chiamo move_and_slide
#così che il moveimento e le collisioni vengano calcolate durante il frame
func _process(delta):
	get_input()
	move_and_slide()

#_input ci permette di controllare che tasti sono premuti. Se il tasto "SPAZIO" è 
#premuto, allora spariamo il laser
func _input(event):
	if event is InputEventKey and event.pressed:
		if event.keycode == KEY_SPACE and can_shoot:
			can_shoot = false
			shoot_laser()
			fire_rate_timer.start()

#Metodo per istanziare la scena laser.tscn nell'albero delle scene.
func shoot_laser() -> void:
	var laserScene = laser.instantiate()
	get_tree().current_scene.add_child(laserScene)
	laserScene.position = position

#--- Segnale da collegare del nodo Timer ---
func _on_timer_timeout() -> void:
	can_shoot = true

func take_damage():
	if not is_immune:
		GameManager.damage()
		blink()

func blink():
	is_immune = true
	#Creiamo un tween dinamicamente
	var tween = create_tween()
	#Lo configuriamo come loop senza argomenti
	tween.set_loops()
	#Il tween cambierà l'alpha channel da 0 a 1, creando l'effetto blink
	tween.tween_property(self, "modulate:a", 0.0, 0.25)
	tween.tween_property(self, "modulate:a", 1.0, 0.25)
	#Creiamo un timer per far girare il blink 3 secondi
	await get_tree().create_timer(3.0).timeout
	#Eliminiamo il tween
	tween.kill()
	modulate.a = 1.0
	is_immune = false
