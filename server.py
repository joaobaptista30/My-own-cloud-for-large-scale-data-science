#! /usr/bin/python3
import logging
from main import APP


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
  
  APP.run(host='0.0.0.0', port=9000)