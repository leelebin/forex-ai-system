import requests
def send(t,c,txt): requests.post(f'https://api.telegram.org/bot{t}/sendMessage',data={'chat_id':c,'text':txt})