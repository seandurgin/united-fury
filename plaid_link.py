import os, json
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid import Configuration, ApiClient, Environment

for line in open('/etc/clawdia/env'):
    line=line.strip()
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1); os.environ[k]=v

CLIENT_ID=os.environ['PLAID_CLIENT_ID']
SECRET=os.environ['PLAID_SECRET']
PLAID_ENV=os.environ.get('PLAID_ENV','sandbox')

env=Environment.Sandbox if PLAID_ENV=='sandbox' else Environment.Production
config=Configuration(host=env,api_key={"clientId":CLIENT_ID,"secret":SECRET})
client=plaid_api.PlaidApi(ApiClient(config))

request=LinkTokenCreateRequest(
    user=LinkTokenCreateRequestUser(client_user_id="sean_durgin"),
    client_name="Clawdia",
    products=[Products("transactions")],
    country_codes=[CountryCode("US")],
    language="en"
)
response=client.link_token_create(request)
link_token=response.link_token

html=f"""<!DOCTYPE html><html><head><title>Connect Bank</title></head>
<body style="font-family:sans-serif;max-width:600px;margin:50px auto;padding:20px">
<h2>Connect Bank Account to Clawdia</h2>
<button id="btn" style="padding:12px 24px;font-size:16px;background:#4CAF50;color:white;border:none;cursor:pointer">Connect Bank Account</button>
<div id="result" style="margin-top:20px;padding:15px;background:#f5f5f5;display:none;word-break:break-all"></div>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
var handler=Plaid.create({{
  token:'{link_token}',
  onSuccess:function(public_token,metadata){{
    var cmd='cd /opt/clawdia && venv/bin/python3 -c "from plaid_finance import exchange_public_token; print(exchange_public_token(\\'' +public_token+ '\\', \\'' +metadata.institution.name+ '\\'))"';
    document.getElementById('result').style.display='block';
    document.getElementById('result').innerHTML='<b>Success! Institution: '+metadata.institution.name+'</b><br><br>Run this command on the server:<br><br><code>'+cmd+'</code>';
  }},
  onExit:function(err){{ if(err) alert(JSON.stringify(err)); }}
}});
document.getElementById('btn').onclick=function(){{handler.open();}};
</script></body></html>"""

with open('/opt/clawdia/plaid_link.html','w') as f:
    f.write(html)
print("plaid_link.html created")
print(f"Link token: {link_token[:20]}...")
