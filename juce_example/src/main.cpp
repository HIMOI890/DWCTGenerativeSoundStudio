#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>

using namespace juce;

static int httpGet(const String& url, String& outBody)
{
    URL u(url);
    StringPairArray headers;
    headers.set("User-Agent", "edmg_juce_client/0.1");
    std::unique_ptr<InputStream> stream(u.createInputStream(URL::InputStreamOptions(URL::ParameterHandling::inAddress)
                                                                .withExtraHeaders(headers.getDescription())
                                                                .withConnectionTimeoutMs(8000)));
    if (!stream)
        return 0;

    outBody = stream->readEntireStreamAsString();
    return 200;
}

int main (int argc, char* argv[])
{
    ConsoleApplication app;

    String baseUrl = "http://127.0.0.1:8000";
    if (argc >= 2)
        baseUrl = argv[1];

    String body;
    auto status = httpGet(baseUrl + "/health", body);

    std::cout << "GET " << (baseUrl + "/health") << " -> " << status << "\n";
    std::cout << body << "\n";
    return status == 200 ? 0 : 2;
}
