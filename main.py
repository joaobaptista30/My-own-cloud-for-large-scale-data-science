import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import render_template, Flask


APP = Flask(__name__)

#pag inicial
@APP.route('/')
def index():
    return render_template('index.html')