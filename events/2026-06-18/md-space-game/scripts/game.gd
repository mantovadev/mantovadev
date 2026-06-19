#Script che contiene le logiche di Game
extends Node2D

@onready var asteroidTimer = $Timer
@onready var scoreLabel = $Label
@onready var lifeContainer = $HBoxContainer

#Preload della scena di Asteroid. In questo modo viene caricata in
#memoria quando la scena Game viene caricata e possiamo usarla quando
#vogliamo senza rallentamenti.
var asteroid = preload("res://scenes/asteroid.tscn")
var asteroidsGenerator = 5

func _ready() -> void:
	#Collego il segnale “update_score” di GameManager dentro il metodo _on_update_score
	#Facciamo la stessa cosa per gli altri due
	GameManager.connect("update_score", _on_update_score)
	GameManager.connect("update_life", _on_update_life)
	GameManager.connect("game_over", _on_game_over)
	generate_asteroids()

#Generiamo gli asteroidi al primo avvio della scena e 
#ogni volta che Timer lancia timeout
func generate_asteroids():
	for x in range(0, asteroidsGenerator):
		var asteroidScene = asteroid.instantiate()
		add_child(asteroidScene)

#--- Segnale da collegare del nodo Timer ---
#Ogni secondo (o ogni wait_time configurato nel nodo Timer
#viene chiamato generate_asteroids()
func _on_timer_timeout() -> void:
	generate_asteroids()

#Metodo chiamato quando GameManager fa emit del segnale update_score. Aggiorna
#la label dei punti
func _on_update_score(score: int):
	scoreLabel.text = str(score)

#Metodo chiamato quando GameManager fa emit del segnale update_life. Aggiorna
#il container degli HP
func _on_update_life(life: int):
	for x in lifeContainer.get_child_count():
		if life > 0:
			lifeContainer.get_children()[x].visible = true
		else:
			lifeContainer.get_children()[x].visible = false
		life-=1

#Metodo chiamato quando GameManager fa emit del segnale game_over.
#Call_deferred perchè non possiamo passare da una scena all'altra
#prima che il motore non abbia finito il ciclo corrente di
#calcoli
func _on_game_over():
	call_deferred("end_game")

#Fine partita che cambia la scena attuale con res://scenes/gameOver.tscn
func end_game():
	get_tree().change_scene_to_file("res://scenes/gameOver.tscn")
