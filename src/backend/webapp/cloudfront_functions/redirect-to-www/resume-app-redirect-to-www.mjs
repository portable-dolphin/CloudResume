const httpHosts = [[BASE_DOMAIN_NAME_PLACEHOLDER]];
const httpsHost = "[BASE_DOMAIN_URL_PLACEHOLDER]";

function handler(event) {
    var request = event.request;
    var host = request.headers.host.value;

    if (httpHosts.includes(host)) {
        var response = {
            statusCode: 301,
            statusDescription: "Moved Permanently",
            headers: {
                location: { value: `${httpsHost}${request.uri}` },
            },
        };

        return response;
    }

    return request;
}
