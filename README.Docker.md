### Local workflow runs

Build the image:

```bash
docker build -t jobsearch-agent:local .
```

Run the local workflow with your Google ADC file mounted automatically:

```bash
./scripts/docker-run-local.sh --workflow daily
```

The helper script expects your ADC file at `~/.config/gcloud/application_default_credentials.json`.
Override that path with `GOOGLE_ADC_FILE=/absolute/path/to/credentials.json`.

Redis defaults to `redis://host.docker.internal:6379/0` for `scripts/docker-run-local.sh`.
Start local Redis with:

```bash
docker run --name jobsearch-redis -p 6379:6379 -d redis:7-alpine
```

Override the Redis URL with `DOCKER_REDIS_URL=redis://HOST:6379/0` if needed.

You can also use Compose:

```bash
export GOOGLE_ADC_FILE="$HOME/.config/gcloud/application_default_credentials.json"
docker compose run --rm jobsearch
docker compose run --rm jobsearch --workflow gmail
docker compose run --rm jobsearch --workflow availability
```

Compose includes a Redis service and defaults `REDIS_URL` to `redis://redis:6379/0`.
Start Redis for Compose with:

```bash
docker compose up -d redis
```

### Deploying your application to the cloud

First, build your image, e.g.: `docker build -t jobsearch-agent:local .`.
If your cloud uses a different CPU architecture than your development
machine (e.g., you are on a Mac M1 and your cloud provider is amd64),
you'll want to build the image for that platform, e.g.:
`docker build --platform=linux/amd64 -t jobsearch-agent:local .`.

Then, push it to your registry.

Consult Docker's [getting started](https://docs.docker.com/go/get-started-sharing/)
docs for more detail on building and pushing.

### References
* [Docker's Python guide](https://docs.docker.com/language/python/)
