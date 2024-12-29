from flask import Flask, request, render_template, make_response, redirect, url_for
from sqlalchemy import create_engine, Column, String, Integer
from sqlalchemy.orm import sessionmaker, declarative_base
from base64 import b64encode
from threading import Thread
import websockets
import asyncio
import random
import json
import uuid
import re

debugmode = False # enable flask debug mode and print message_payload json dump

def is_valid_input(value):
    return re.match(r"^\w+$", value) is not None

app = Flask(__name__)

DATABASE_URL = "sqlite:///socktalk.db"
engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)
session = Session()

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    api_key = Column(String, unique=True, nullable=False)
    user_id = Column(String, unique=True, nullable=False)
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)

Base.metadata.create_all(engine)

def generate_api_key():
    uuids = [str(uuid.uuid4()) for _ in range(3)]
    shuffled = "".join(random.sample(uuids, len(uuids)))
    return b64encode(shuffled.encode()).decode()

def generate_user_id():
    return str(uuid.uuid4())

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/exampleclient")
def exampleclient():
    return render_template("exampleclient.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password or not is_valid_input(username) or not is_valid_input(password):
            return render_template("signup.html", error="Invalid username or password. Only alphanumeric characters and underscores are allowed.")

        if session.query(Account).filter_by(username=username).first():
            return render_template("signup.html", error="Username already exists.")

        api_key = generate_api_key()
        user_id = generate_user_id()
        new_account = Account(api_key=api_key, user_id=user_id, username=username, password=password)
        session.add(new_account)
        session.commit()

        response = make_response(redirect(url_for("home")))
        response.set_cookie("api_key", api_key, max_age=7 * 24 * 60 * 60)
        return response

    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        account = session.query(Account).filter_by(username=username, password=password).first()
        if not account:
            return render_template("login.html", error="Invalid username or password.")

        response = make_response(redirect(url_for("home")))
        response.set_cookie("api_key", account.api_key, max_age=7*24*60*60)
        return response

    return render_template("login.html")

@app.route("/logout")
def logout():
    response = make_response(redirect(url_for("home")))
    response.delete_cookie("api_key")
    return response

@app.route("/profile", methods=["GET", "POST"])
def profile():
    api_key = request.cookies.get("api_key")
    if not api_key:
        return redirect(url_for("login"))

    account = session.query(Account).filter_by(api_key=api_key).first()
    if not account:
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "change_username":
            new_username = request.form.get("new_username")
            if not new_username or not is_valid_input(new_username) or session.query(Account).filter_by(username=new_username).first():
                return render_template("profile.html", username=account.username, api_key=account.api_key, error="Invalid or duplicate username.")

            account.username = new_username
            session.commit()

        elif action == "reset_api_key":
            new_api_key = generate_api_key()
            account.api_key = new_api_key
            session.commit()
            response = make_response(render_template("profile.html", username=account.username, api_key=new_api_key))
            response.set_cookie("api_key", new_api_key, max_age=7 * 24 * 60 * 60)
            return response

        elif action == "change_password":
            old_password = request.form.get("old_password")
            new_password = request.form.get("new_password")
            if account.password != old_password or not new_password or not is_valid_input(new_password):
                return render_template("profile.html", username=account.username, api_key=account.api_key, error="Invalid password or missing new password.")

            account.password = new_password
            session.commit()

    response = make_response(render_template("profile.html", username=account.username, api_key=account.api_key))
    response.set_cookie("api_key", account.api_key, max_age=7 * 24 * 60 * 60)
    return response

# Actual server

connected_clients = []

async def handle_client(websocket, path):
    try:
        connected_clients.append(websocket)
        print(f"New client connected. Total clients: {len(connected_clients)}")

        async for message in websocket:
            data = json.loads(message)
            action = data.get("action")
            api_key = data.get("api_key")

            account = session.query(Account).filter_by(api_key=api_key).first()
            if not account:
                await websocket.send(json.dumps({"status": 401, "message": "Invalid API key."}))
                continue

            username = account.username

            if action == "send_message":
                message_text = data.get("message")
                if not message_text:
                    await websocket.send(json.dumps({"status": 400, "message": "Message field is required."}))
                    continue
                
                message_payload = {
                    "sender": {"username": username},
                    "message": message_text,
                }

                extra_fields = {key: value for key, value in data.items() if key not in ["action", "api_key", "message"]}
                message_payload.update(extra_fields)

                disconnected_clients = []
                for client in connected_clients:
                    if client != websocket:
                        try:
                            await client.send(json.dumps(message_payload))
                            if debugmode: print("Message sent: " + json.dumps(message_payload))
                        except websockets.ConnectionClosed:
                            disconnected_clients.append(client)

                for client in disconnected_clients:
                    connected_clients.remove(client)

                await websocket.send(json.dumps({"status": 200, "message": "Message sent successfully."}))

    except websockets.ConnectionClosed:
        print("WebSocket connection closed.")
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
        print(f"Client disconnected. Total clients: {len(connected_clients)}")

def run_flask():
    app.run(host='0.0.0.0', port=80, debug=debugmode, use_reloader=False)

async def run_websocket():
    async with websockets.serve(handle_client, "0.0.0.0", 443):
        print("WebSocket server running on ws://0.0.0.0:443")
        await asyncio.Future()

if __name__ == "__main__":
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    asyncio.run(run_websocket())