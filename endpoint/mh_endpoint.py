#!/usr/bin/env python3

import json
import ssl
import sys
import os
import requests
from datetime import datetime
import time
import config
from http.client import HTTPConnection
import base64
from collections import OrderedDict

from http.server import BaseHTTPRequestHandler, HTTPServer

from register import apple_cryptography, pypush_gsa_icloud

import logging
logger = logging.getLogger()


class ServerHandler(BaseHTTPRequestHandler):
    
    def addCORSHeaders(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Headers", "Authorization")

    def authenticate(self):
        user = config.getEndpointUser()
        passw = config.getEndpointPass()
        if (user is None or user == "") and (passw is None or passw == ""):
            return True

        auth_header = self.headers.get('authorization')
        if auth_header:
            auth_type, auth_encoded = auth_header.split(None, 1)
            if auth_type.lower() == 'basic':
                auth_decoded = base64.b64decode(auth_encoded).decode('utf-8')
                username, password = auth_decoded.split(':', 1)
                if username == user and password == passw:
                    return True

        return False

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.addCORSHeaders()
        self.end_headers()

    def do_GET(self):
        if not self.authenticate():
            self.send_response(401)
            self.addCORSHeaders()
            self.send_header('WWW-Authenticate', 'Basic realm="Auth Realm"')
            self.end_headers()
            return
        self.send_response(200)
        self.addCORSHeaders()
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Nothing to see here")

    def do_POST(self):
        if not self.authenticate():
            self.send_response(401)
            self.addCORSHeaders()
            self.send_header('WWW-Authenticate', 'Basic realm="Auth Realm"')
            self.end_headers()
            return
        if hasattr(self.headers, 'getheader'):
            content_len = int(self.headers.getheader('content-length', 0))
        else:
            content_len = int(self.headers.get('content-length'))

        post_body = self.rfile.read(content_len)

        logger.debug('Getting with post: ' + str(post_body))
        body = json.loads(post_body)
        if "days" in body:
            days = body['days']
        else:
            days = 7
        logger.debug('Querying for ' + str(days) + ' days')
        unixEpoch = int(datetime.now().strftime('%s'))
        startdate = unixEpoch - (60 * 60 * 24 * days)

        dt_object = datetime.fromtimestamp(startdate)

        # Date is always 1, because it has no effect
        data = {"search": [
            {"startDate": 1, "ids": list(body['ids'])}]}

        try:
            r = requests.post("https://gateway.icloud.com/acsnservice/fetch",  auth=getAuth(regenerate=False, second_factor='sms'),
                              headers=pypush_gsa_icloud.generate_anisette_headers(),
                              json=data)
            logger.debug('Return from fetch service:')
            logger.debug(r.content.decode())
            result = json.loads(r.content.decode())
            results = result['results']

            newResults = OrderedDict()

            for idx, entry in enumerate(results):
                data = base64.b64decode(entry['payload'])
                timestamp = int.from_bytes(data[0:4], 'big') + 978307200
                if (timestamp > startdate):
                    newResults[timestamp] = entry

            sorted_map = OrderedDict(sorted(newResults.items(), reverse=True))

            result["results"] = list(sorted_map.values())
            self.send_response(200)
            # send response headers
            self.addCORSHeaders()
            self.end_headers()

            # send the body of the response
            responseBody = json.dumps(result)
            self.wfile.write(responseBody.encode())
        except requests.exceptions.ConnectTimeout:
            logger.error("Timeout to " + config.getAnisetteServer() +
                         ", is your anisette running and accepting Connections?")
            self.send_response(504)
        except Exception as e:
            logger.error("Unknown error occured {e}", exc_info=True)
            self.send_response(501)

    def getCurrentTimes(self):
        clientTime = datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
        clientTimestamp = int(datetime.now().strftime('%s'))
        return clientTime, time.tzname[1], clientTimestamp


def getAuth(regenerate=False, second_factor='sms'):
    if os.path.exists(config.getConfigFile()) and not regenerate:
        with open(config.getConfigFile(), "r") as f:
            j = json.load(f)
    else:
        mobileme = pypush_gsa_icloud.icloud_login_mobileme(username=config.USER, password=config.PASS,
                                                           second_factor=second_factor)
        logger.debug('Mobileme result: ' + mobileme)
        j = {'dsid': mobileme['dsid'], 'searchPartyToken': mobileme['delegates']
             ['com.apple.mobileme']['service-data']['tokens']['searchPartyToken']}
        with open(config.getConfigFile(), "w") as f:
            json.dump(j, f)
    return (j['dsid'], j['searchPartyToken'])


if __name__ == "__main__":

    logging.debug(f'Searching for token at ' + config.getConfigFile())
    if not os.path.exists(config.getConfigFile()):
        logging.info(f'No auth-token found.')
        apple_cryptography.registerDevice()

    Handler = ServerHandler
    httpd = HTTPServer(('0.0.0.0', config.getPort()), Handler)
    if os.path.isfile(config.getCertFile()):
        logger.info("Certificate file " + config.getCertFile() +
                    " exists, so using SSL")
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile=config.getCertFile(
        ), keyfile=config.getKeyFile() if os.path.isfile(config.getKeyFile()) else None)

        httpd.socket = ssl_context.wrap_socket(httpd.socket, server_side=True)

        logger.info("serving at port " + str(config.getPort()) + " over HTTPS")
    else:
        logger.info("Certificate file " + config.getCertFile() +
                    " not found, so not using SSL")
        logger.info("serving at port " + str(config.getPort()) + " over HTTP")
    user = config.getEndpointUser()
    passw = config.getEndpointPass()
    if (user is None or user == "") and (passw is None or passw == ""):
        logger.warning("Endpoint is not protected by authentication")
    else:
        logger.info("Endpoint is protected by authentication")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        logger.info('Server stopped')
