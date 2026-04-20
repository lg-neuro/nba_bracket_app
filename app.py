from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-change-me'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///brackets.db'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    is_admin = db.Column(db.Boolean, default=False) # NEW: Admin flag
    brackets = db.relationship('Bracket', backref='owner', lazy=True)

class League(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) 
    brackets = db.relationship('Bracket', backref='league', lazy=True)

class Bracket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    data = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    league_id = db.Column(db.Integer, db.ForeignKey('league.id'), nullable=True)

# NEW: Stores the single source of truth for the real world
class OfficialResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Text, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- NEW: SCORING LOGIC ---
def calculate_score(user_data_str, master_data_str):
    if not master_data_str:
        return 0
    
    user_data = json.loads(user_data_str)
    master_data = json.loads(master_data_str)
    score = 0
    
    # Points allocation based on your exact rules
    rounds = {
        'w-r1-m1': 10, 'w-r1-m2': 10, 'w-r1-m3': 10, 'w-r1-m4': 10,
        'e-r1-m1': 10, 'e-r1-m2': 10, 'e-r1-m3': 10, 'e-r1-m4': 10,
        'w-r2-m1': 15, 'w-r2-m2': 15, 'e-r2-m1': 15, 'e-r2-m2': 15,
        'w-r3-m1': 20, 'e-r3-m1': 20,
        'finals-m': 25
    }
    
    for matchup, pts in rounds.items():
        master_winner = master_data.get(f"{matchup}-winner")
        # Only score games that the Admin has officially decided
        if master_winner and master_winner != "TBD":
            user_winner = user_data.get(f"{matchup}-winner")
            
            # Correct Winner
            if user_winner == master_winner:
                score += pts
                
                # Correct Loser Games (Bonus 5 pts)
                master_loser_score = master_data.get(f"{matchup}-loserScore")
                user_loser_score = user_data.get(f"{matchup}-loserScore")
                if master_loser_score and user_loser_score == master_loser_score:
                    score += 5
                    
    return score

# This makes the scoring function available directly in HTML templates
@app.context_processor
def utility_processor():
    def get_bracket_score(bracket_data_json):
        official = OfficialResult.query.first()
        if not official:
            return 0
        return calculate_score(bracket_data_json, official.data)
    return dict(get_bracket_score=get_bracket_score)


# --- Routes ---
@app.route('/')
def index():
    if current_user.is_authenticated:
        user_brackets = Bracket.query.filter_by(user_id=current_user.id).order_by(Bracket.created_at.desc()).all()
        return render_template('index.html', brackets=user_brackets)
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Username already exists.')
            return redirect(url_for('register'))
        
        # NEW: Automatically make the user 'admin' the superuser
        is_admin = True if username.lower() == 'admin' else False
        new_user = User(username=username, password=generate_password_hash(password, method='pbkdf2:sha256'), is_admin=is_admin)
        
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST']) # ... (keep existing login code)
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password.')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/bracket', methods=['GET'])
@login_required
def new_bracket():
    return render_template('bracket.html')

@app.route('/api/save_bracket', methods=['POST']) 
@login_required
def save_bracket():
    req_data = request.get_json()
    bracket_name = req_data.get('name', 'My Bracket')
    bracket_data = req_data.get('data', {})
    new_bracket = Bracket(name=bracket_name, data=json.dumps(bracket_data), user_id=current_user.id)
    db.session.add(new_bracket)
    db.session.commit()
    return jsonify({"message": "Bracket saved successfully!"}), 200

@app.route('/delete_bracket/<int:bracket_id>', methods=['POST']) 
@login_required
def delete_bracket(bracket_id):
    bracket = Bracket.query.get_or_404(bracket_id)
    if bracket.user_id != current_user.id:
        flash("You do not have permission to delete this bracket.")
        return redirect(url_for('index'))
    db.session.delete(bracket)
    db.session.commit()
    flash(f"Bracket '{bracket.name}' was deleted.")
    return redirect(url_for('index'))

@app.route('/bracket/<int:bracket_id>')
@login_required
def view_bracket(bracket_id):
    bracket = Bracket.query.get_or_404(bracket_id)
    if bracket.user_id != current_user.id and bracket.league_id is None and not current_user.is_admin:
        flash("You do not have permission to view this private bracket.")
        return redirect(url_for('index'))
    
    bracket_data = json.loads(bracket.data)
    return render_template('view_bracket.html', bracket=bracket, bracket_data=bracket_data)

# --- NEW: Route for Admin to define the real world results ---
@app.route('/set_official/<int:bracket_id>', methods=['POST'])
@login_required
def set_official(bracket_id):
    if not current_user.is_admin:
        flash("Unauthorized action.")
        return redirect(url_for('index'))
        
    bracket = Bracket.query.get_or_404(bracket_id)
    official = OfficialResult.query.first()
    
    if not official:
        official = OfficialResult(data=bracket.data)
        db.session.add(official)
    else:
        official.data = bracket.data
        
    db.session.commit()
    flash("Official real-world results updated! All user scores have been recalculated.", "success")
    return redirect(url_for('view_bracket', bracket_id=bracket.id))

@app.route('/leagues', methods=['GET', 'POST']) # ... (keep existing leagues code)
@login_required
def leagues():
    if request.method == 'POST':
        league_name = request.form.get('league_name')
        if League.query.filter_by(name=league_name).first():
            flash("That League name is already taken. Try another.")
        else:
            new_league = League(name=league_name, creator_id=current_user.id)
            db.session.add(new_league)
            db.session.commit()
            flash(f"League '{league_name}' created!")
        return redirect(url_for('leagues'))
    all_leagues = League.query.all()
    available_brackets = Bracket.query.filter_by(user_id=current_user.id, league_id=None).all()
    return render_template('leagues.html', leagues=all_leagues, available_brackets=available_brackets)

@app.route('/delete_league/<int:league_id>', methods=['POST']) # ... (keep existing code)
@login_required
def delete_league(league_id):
    league = League.query.get_or_404(league_id)
    if league.creator_id != current_user.id:
        flash("You do not have permission to delete this league.")
        return redirect(url_for('leagues'))
    for bracket in league.brackets:
        bracket.league_id = None
    db.session.delete(league)
    db.session.commit()
    flash(f"League '{league.name}' has been deleted.")
    return redirect(url_for('leagues'))

@app.route('/league/<int:league_id>')
@login_required
def view_league(league_id):
    league = League.query.get_or_404(league_id)
    return render_template('view_league.html', league=league)

@app.route('/join_league/<int:league_id>', methods=['POST'])
@login_required
def join_league(league_id):
    bracket_id = request.form.get('bracket_id')
    if bracket_id:
        bracket = Bracket.query.get(bracket_id)
        if bracket and bracket.user_id == current_user.id and bracket.league_id is None:
            bracket.league_id = league_id
            db.session.commit()
            flash(f"Successfully joined the league with bracket '{bracket.name}'!")
    return redirect(url_for('view_league', league_id=league_id))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)