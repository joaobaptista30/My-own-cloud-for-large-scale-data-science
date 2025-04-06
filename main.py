import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import render_template, Flask


APP = Flask(__name__)

#pag inicial
@APP.route('/')
def index():
    return render_template('index.html')



@APP.route('/profile')
def profile():
    return render_template('underdev.html')



@APP.route('/virtualmachine')
def vm():
    return render_template('underdev.html')



@APP.route('/diskstorage')
def diskstorage():
    return render_template('underdev.html')



@APP.route('/container')
def containers():
    return render_template('underdev.html')



@APP.route('/database')
def databased():
    return render_template('underdev.html')