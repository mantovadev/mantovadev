#Autoload script GameManager
extends Node2D

var hp = 3
var best_score = 0
var current_score = 0

#Segnali per comunicare con altri nodi degli eventi
signal update_score(score: int)
signal update_life(life: int)
signal game_over()

func damage():
	hp -= 1
	update_life.emit(hp)
	if hp <= 0:
		if current_score > best_score:
			best_score = current_score
		game_over.emit()

func increase_score(is_golden: bool):
	if is_golden:
		current_score+=5
	else:
		current_score+=1
	update_score.emit(current_score)

func reset():
	current_score = 0
	hp = 3
