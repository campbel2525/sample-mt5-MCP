init: ## 開発作成
	docker compose build --no-cache
	docker compose down --volumes
	docker compose up -d
	docker compose exec -it app pipenv install --dev

up: ## 開発立ち上げ
	docker compose up -d

down: ## 開発down
	docker compose down

shell: ## dockerのshellに入る
	docker compose exec app bash

check: ## コードのフォーマット
# app
	docker compose exec -it app pipenv run isort .
	docker compose exec -it app pipenv run black .
	docker compose exec -it app pipenv run flake8 .
	docker compose exec -it app pipenv run mypy .

push:
	git pull origin HEAD
	git add .
	git commit -m "Commit at $$(date +'%Y-%m-%d %H:%M:%S')"
	git push origin HEAD

reset-commit: ## mainブランチのコミット履歴を1つにする 使用は控える
	git pull origin HEAD
	git checkout --orphan new-branch-name
	git add .
	git branch -D main
	git branch -m main
	git commit -m "first commit"
	git push origin -f main

test:
	docker compose exec -it app pipenv run pytest tests/
