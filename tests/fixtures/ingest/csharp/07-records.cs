// Records, structs and enums.
public record Point(int X, int Y);

public record class Size(int Width, int Height)
{
    public int Area() => Width * Height;
}

public struct Vec2
{
    public double X;
    public double Y;
}

public enum Level
{
    Low = 1,
    High = 2,
}
