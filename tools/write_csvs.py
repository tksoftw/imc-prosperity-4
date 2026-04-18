import json
# import jsontreeview

fn = 'logs/round1_data/112960.log'

with open(fn) as infile:
    data = json.load(infile)
    # keys: 'submissionId', 'activitiesLog', 'logs', 'tradeHistory'
    # submissionId  -> str
    # activitiesLog -> csv
    # logs          -> json
    # tradeHistory  -> json

with open(fn.replace('.log', '_activities.csv'), 'w') as f:
    f.write(data['activitiesLog'])

with open(fn.replace('.log', '_logs.json'), 'w') as f:
    json.dump(data['logs'], f, indent=4)

with open(fn.replace('.log', '_trade_history.json'), 'w') as f:
    json.dump(data['tradeHistory'], f, indent=4)
