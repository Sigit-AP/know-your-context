// using directives: plain, static and aliased.
using System;
using System.Text;
using static System.Math;
using Json = System.Text.Json;

public class Report
{
    public string Render()
    {
        var builder = new StringBuilder();
        builder.Append(Sqrt(4));
        return builder.ToString();
    }
}
