# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Launch a private demo server and exercise the UI. Physical serial is disabled."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from browser_support import isolated_dashboard

with isolated_dashboard() as (base_url, artifacts), sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1512,'height':1100},device_scale_factor=1)
    errors=[]
    page.on('pageerror',lambda exc:errors.append(str(exc)))
    page.goto(base_url + '/')
    expect(page.get_by_text('Recorder online',exact=True)).to_be_visible()
    state=page.request.get(base_url + '/api/state').json()
    assert state['mode']=='disconnected', 'Use an isolated, disconnected preview server'
    assert not state['profiles'] and not state['runs'], 'Expected a fresh temporary database'
    blocked=page.request.post(base_url + '/api/connect', data={'port':'/dev/celltrace-test-disabled'}, headers={'Origin':base_url})
    assert blocked.status==409 and 'Physical serial disabled' in blocked.text()
    page.get_by_role('button',name='Try demo',exact=True).click()
    expect(page.get_by_text('SIMULATED • accelerated',exact=True)).to_be_visible()
    page.get_by_role('button',name='New profile',exact=True).click()
    dialog=page.get_by_role('dialog',name='Battery profile')
    dialog.get_by_label('Profile name',exact=True).fill('Browser validation pack')
    dialog.get_by_label('Battery identifier').fill('SIM-QA')
    dialog.get_by_label('Nominal voltage').fill('12.8')
    dialog.get_by_label('Expected capacity').fill('0.5')
    dialog.get_by_label('Expected energy').fill('6.4')
    dialog.get_by_label('Source label').fill('Synthetic validation curve')
    dialog.get_by_label('Points:').fill('ah,voltage_v\n0,13.3\n0.25,13.0\n0.5,10')
    dialog.get_by_role('button',name='Save profile').click()
    expect(dialog).not_to_be_visible()
    page.get_by_role('button',name='Start test',exact=True).click()
    page.get_by_role('button',name='Verify settings & arm').click()
    expect(page.get_by_text('RUNNING',exact=True)).to_be_visible(timeout=10000)
    first=page.request.get(base_url + '/api/state').json()['current_run']['id']
    page.reload()
    expect(page.get_by_text('Recorder online',exact=True)).to_be_visible()
    assert page.request.get(base_url + '/api/state').json()['current_run']['id']==first
    page.get_by_role('button',name='Expected vs actual',exact=True).click()
    expect(page.get_by_text('Synthetic validation curve',exact=False)).to_be_visible()
    page.screenshot(path=str(artifacts / 'live.png'),full_page=True)
    expect(page.get_by_role('button',name='View report')).to_be_visible(timeout=15000)
    page.get_by_role('button',name='View report').click()
    expect(page.get_by_text('FROZEN DEVICE REPORT',exact=True)).to_be_visible()
    expect(page.get_by_text('SIMULATED DATA — no battery measurement',exact=False)).to_be_visible()
    page.get_by_label('Test notes').fill('Verified in the browser without hardware.')
    page.get_by_role('button',name='Save notes').click()
    csv=page.request.get(f'{base_url}/api/runs/{first}/samples.csv')
    assert csv.status==200 and 'voltage_v' in csv.text()
    page.get_by_role('button',name='Test library',exact=True).click()
    expect(page.get_by_role('table')).to_be_visible()
    page.set_viewport_size({'width':390,'height':844})
    page.get_by_role('button',name='Live workspace',exact=True).click()
    page.screenshot(path=str(artifacts / 'mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Horizontal overflow'
    page.set_viewport_size({'width':1512,'height':1100})
    page.get_by_role('button',name='Start test',exact=True).click()
    page.get_by_role('button',name='Verify settings & arm').click()
    expect(page.get_by_text('RUNNING',exact=True)).to_be_visible(timeout=10000)
    page.get_by_role('button',name='Stop recording',exact=True).click()
    expect(page.get_by_role('button',name='View report')).to_be_visible(timeout=10000)
    page.get_by_label('Baseline test',exact=True).select_option(first)
    expect(page.get_by_text('Baseline: Browser validation pack',exact=False)).to_be_visible()
    page.get_by_role('button',name='Current / power',exact=True).click()
    with page.expect_download() as download:
        page.get_by_role('button',name='Export chart PNG',exact=True).click()
    assert Path(download.value.path()).read_bytes().startswith(b'\x89PNG')
    page.get_by_role('button',name='Save baseline as reference',exact=True).click()
    dialog=page.get_by_role('dialog',name='Battery profile')
    expect(dialog.get_by_role('combobox',name='Reference source',exact=True)).to_have_value('previous measurement')
    dialog.get_by_role('button',name='Save profile').click()
    expect(dialog).not_to_be_visible()
    assert not errors,errors
    page.request.post(base_url + '/api/disconnect',data={})
    browser.close()
    print('PASS: profiles, reference import, live chart, refresh continuity, completion, report, notes, CSV/PNG exports, baseline overlay, reference reuse, responsive layout, no browser errors')
