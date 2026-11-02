// Namespace-level constants and fields.
namespace Demo {
    public const int LIMIT = 100;

    public const string DEFAULT_NAME = "demo";

    public int counter = 0;

    public int next() {
        counter += 1;
        return counter;
    }

    public string describe() {
        return DEFAULT_NAME;
    }
}
