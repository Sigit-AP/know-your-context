// Invocation expressions parking unresolved refs.
public class Runner
{
    private int Helper(int value) => value * 2;

    public int Run(int seed)
    {
        var total = Helper(seed);
        return Helper(total);
    }
}
