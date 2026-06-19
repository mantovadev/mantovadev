#Script che contiene le logiche di GameOver
extends Control

@onready var score = $CenterContainer/VBoxContainer/Label2
@onready var best_score = $CenterContainer/VBoxContainer/Label3

func _ready() -> void:
	score.text += " " + str(GameManager.current_score)
	best_score.text += " " + str(GameManager.best_score)

#--- Segnale da collegare del nodo Button ---
#Reset del GameManager e cambio scena a quella di gioco
func _on_button_pressed() -> void:
	GameManager.reset()
	get_tree().change_scene_to_file("res://scenes/game.tscn")
