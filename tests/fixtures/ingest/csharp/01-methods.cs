// Static and instance methods in a class.
namespace Demo;

public class Calculator
{
    public int Add(int a, int b) => a + b;

    public static int Clamp(int value, int low, int high)
    {
        if (value < low) return low;
        if (value > high) return high;
        return value;
    }

    private string Describe() => "calculator";
}
