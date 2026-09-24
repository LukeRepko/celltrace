# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Start failure UI regression. Every API and WebSocket request is mocked."""
import json
from playwright.sync_api import sync_playwright, expect
from browser_support import isolated_dashboard

profile={'id':'fixture','name':'Test fixture','battery_id':'','chemistry':'LiFePO4','nominal_v':12.8,'expected_ah':20,'expected_wh':256,'endpoint_v':10,'reference':[],'conditions':''}
sample={'test_id':'0','uptime_ms':'1000','state':'IDLE','valid':'1','voltage_v':'13.2000','current_a':'0.0000','power_w':'0.0000','elapsed_s':'0.000','ah':'0.000000','mah':'0.000','wh':'0.000000','kwh':'0.00000000','remaining_est_pct':'','rated_ah_pct':'0.00','rated_wh_pct':'0.00','reason':'none'}
base={'mode':'serial','port':'MOCK SERIAL','baud':115200,'sample':sample,'age':0,'settings':None,'pending':False,'csv':True,'current_run':None,'events':[],'raw':[],'profiles':[profile],'runs':[]}
with isolated_dashboard() as (base_url, artifacts), sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    for protocol in (0,1):
        page=browser.new_page(viewport={'width':1440,'height':1000})
        snapshot={**base,'protocol':protocol}
        page.route_web_socket('**/ws',lambda ws,s=snapshot:ws.send(json.dumps({'state':s,'points':[]})))
        def respond(route):
            if route.request.url.endswith('/api/start'):
                route.fulfill(status=409,content_type='application/json',body=json.dumps({'detail':'Device refused test: simulated validation error'}))
            else:route.fulfill(status=200,content_type='application/json',body='[]')
        page.route('**/api/**',respond)
        page.goto(base_url)
        start=page.get_by_role('button',name='Start test',exact=True)
        if protocol==0:
            expect(start).to_be_disabled()
            expect(page.get_by_role('alert')).to_contain_text('Firmware update required')
        else:
            expect(start).to_be_enabled();start.click()
            page.get_by_role('button',name='Verify settings & arm').click()
            dialog=page.get_by_role('dialog',name='Start test',exact=True)
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role('alert')).to_contain_text('simulated validation error')
        page.close()
    browser.close()
print('PASS: legacy firmware is clearly blocked; failed start remains visible in dialog. All device traffic mocked.')
