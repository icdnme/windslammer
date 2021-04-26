import asyncio
import aiohttp
import aiohttp.web
import csv
import io
import json
import os
import time

def compute_bin_id(datum):
    try:
        wind_dir_bin_step = 10
        wind_speed_bin_step = 2

        num_wind_dir_bins = 360/wind_dir_bin_step
        wind_dir_bin = (datum['wind_dir'] % 360) // wind_dir_bin_step
        wind_speed_bin = datum['wind_speed'] // wind_speed_bin_step
        bin_id = int(wind_speed_bin * num_wind_dir_bins + wind_dir_bin)
        return bin_id
    except:
        return -1

async def record_influx(text):
    d = {}
    row = next(csv.reader(io.StringIO(text)))
    for kv in row:
        key, value = kv.split('=')
        d[key] = float(value)
    d['bin'] = compute_bin_id(d)
    new_text = ','.join(f'{k}={v}' for k, v in d.items())
    influx_write_request = f'sensors {new_text}'
    print(influx_write_request)
    async with aiohttp.ClientSession() as session:
        async with session.post('http://localhost:8086/api/v2/write?org=alexhonda.com&bucket=windslammer',
                                data=influx_write_request,
                                headers={'Authorization':f'Token {os.environ["INFLUX_DB_TOKEN"]}'}) as response:
            if response.status < 200 or response.status >= 300:
                e = await response.text()
                print(f'{response.status} {e}')

async def write_sample(session):
    async with session.post('http://windslammer.wingsofrogallo.org/cgi-bin/ws.cgi', data="SNAPSHOT") as response:
        if response.status != 200:
            e = await response.text()
            print(f'{response.status} {e}')
            return
        text = await response.text()
        await record_influx(text)

async def scrape():
    async with aiohttp.ClientSession() as session:
        while True:
            asyncio.ensure_future(write_sample(session))
            await asyncio.sleep(1)

async def influx_query(session, flux_query):
    async with session.post('http://localhost:8086/api/v2/query?org=alexhonda.com',
        data=flux_query,
        headers = {
            'Authorization': f'Token {os.environ["INFLUX_DB_TOKEN"]}',
            'Accept': 'application/csv',
            'Content-type': 'application/vnd.flux'
        }) as response:
            return await response.text()

async def serve(request):
    async with aiohttp.ClientSession() as session:
        try:
            request_json = await request.json()
            request_type = request_json.get('type', 'histogram')
            window = request_json.get('window', 1)
            if request_type == 'histogram':
                flux_query = f'from(bucket: "windslammer") |> range(start:-{window}m, stop:now()) |> filter(fn: (r) => r._field=="bin") |> histogram(bins:linearBins(start:0.0,width:1.0,count:1440))'
                response = await influx_query(session, flux_query)
                response_json = []
                data = {}
                max_bin_top = "0"
                for row in csv.DictReader((await influx_query(session, flux_query)).split('\n')):
                    count = int(row['_value'])
                    bin_top = row['le']
                    data[bin_top] = count
                    try:
                        if int(max_bin_top) < int(row['le']):
                            max_bin_top = row['le']
                    except ValueError:
                        pass
                for k in data:
                    try:
                        i = int(k)
                        prev_count = data[str(i-1)] if i > 0 else 0
                    except ValueError:
                        # This catches upperbound of +Inf
                        i = -1
                        prev_count = data[max_bin_top]
                    curr_count = data[k]-prev_count
                    if (curr_count > 0):
                        response_json.append((i, curr_count))
            else:
                fields = [ ('_time', str), ('wind_dir', float), ('wind_speed', float), ('temp_lo', float), ('temp_hi', float)]
                flux_query = f'from(bucket:"windslammer")|> range(start: -{window}m) |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value") |> keep(columns:["_time","temp_lo","temp_hi","wind_dir","wind_speed"])'
                response_json = []
                for row in csv.DictReader((await influx_query(session, flux_query)).split('\n')):
                    response_json.append([t(row[f]) for f, t in fields])
        except json.JSONDecodeError:
            flux_query = f'from(bucket:"windslammer")|> range(start: -1m) |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value") |> keep(columns:["_time","temp_lo","temp_hi","wind_dir","wind_speed"])'
            response_json = await influx_query(session, flux_query)
    return aiohttp.web.Response(text=json.dumps(response_json, separators=(',', ':')), headers={"Access-Control-Allow-Origin": "*"})

async def start_server():
    server = aiohttp.web.Server(serve)
    runner = aiohttp.web.ServerRunner(server)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', 9001)
    await site.start()
    await asyncio.sleep(1000)

async def main():
    await asyncio.gather(
            scrape(),
            start_server(),
    )


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
