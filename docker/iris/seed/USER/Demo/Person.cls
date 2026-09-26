/// A simple persistent class used by the IRISFS tests.
Class Demo.Person Extends %Persistent
{

Property Name As %String(MAXLEN = 100) [ Required ];

Property DOB As %Date;

Property Email As %String(MAXLEN = 200);

/// Returns the age in whole years.
Method Age() As %Integer
{
    quit:..DOB="" ""
    quit ($horolog - ..DOB) \ 365.25
}

}
