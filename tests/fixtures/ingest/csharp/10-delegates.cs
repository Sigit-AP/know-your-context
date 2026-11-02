// Delegates, events and Func/Action fields.
public delegate int Transformer(int value);

public class Pipeline
{
    private readonly Transformer _step;

    public Pipeline(Transformer step)
    {
        _step = step;
    }

    public int Run(int value) => _step(value);
}
