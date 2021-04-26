import asyncio
import aiohttp
import aiohttp.web
import csv
import json
import time

async def record_influx(text):
    influx_write_request = f'sensors {text}'
    async with aiohttp.ClientSession() as session:
        async with session.post('http://localhost:8086/api/v2/write?org=alexhonda.com&bucket=windslammer',
                                data=influx_write_request,
                                headers={'Authorization':'Token <TOKEN_GOES_HERE>'}) as response:
            if response.status < 200 or response.status >= 300:
                print(f'{response.status} {await response.text()}')

async def write_sample(session):
    async with session.post('http://windslammer.wingsofrogallo.org/cgi-bin/ws.cgi', data="SNAPSHOT") as response:
        if response.status != 200:
            print(f'{response.status} {await response.text()}')
            return
        text = await response.text()
        await record_influx(text)

async def scrape():
    async with aiohttp.ClientSession() as session:
        while True:
            asyncio.ensure_future(write_sample(session))
            await asyncio.sleep(1)

async def serve(request):
    response_json = []
    fields = [ ('_time', str), ('wind_dir', float), ('wind_speed', float), ('temp_lo', float), ('temp_hi', float)]
    async with aiohttp.ClientSession() as session:
        try:
            request_json = await request.json()
            request_type = request_json.get('type', 'histogram')
            window = request_json.get('window', 1)
            if request_type == 'histogram':
                flux_query = f'from(bucket:"windslammer")|> range(start: -{window}m) |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value") |> keep(columns:["_time","temp_lo","temp_hi","wind_dir","wind_speed"])'
            else:
                flux_query = f'from(bucket:"windslammer")|> range(start: -{window}m) |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value") |> keep(columns:["_time","temp_lo","temp_hi","wind_dir","wind_speed"])'
        except json.JSONDecodeError:
            flux_query = f'from(bucket:"windslammer")|> range(start: -1m) |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value") |> keep(columns:["_time","temp_lo","temp_hi","wind_dir","wind_speed"])'
        async with session.post('http://localhost:8086/api/v2/query?org=alexhonda.com',
            data=flux_query,
            headers = {
                'Authorization': 'Token <TOKEN_GOES_HERE>',
                'Accept': 'application/csv',
                'Content-type': 'application/vnd.flux'
            }) as response:
                for row in csv.DictReader((await response.text()).split('\n')):
                    response_json.append([t(row[f]) for f, t in fields])
    return aiohttp.web.Response(text=json.dumps(response_json, separators=(',', ':')))

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
