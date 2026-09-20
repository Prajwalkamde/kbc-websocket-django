from django.core.management.base import BaseCommand
from game.models import Question

QUESTIONS = [
    ("Which Java keyword is used to declare a constant variable?", "static", "final", "const", "volatile", "B", "Java", "EASY"),
    ("Which command is used to create a new Git branch?", "git checkout -b", "git branch new", "git start", "git create", "A", "Programming", "EASY"),
    ("Which protocol is used for secure web communication?", "HTTP", "FTP", "HTTPS", "SMTP", "C", "Programming", "EASY"),
    ("In Java, which access modifier allows visibility only within the same class?", "public", "private", "protected", "default", "B", "Java", "EASY"),
    ("Which one is a JavaScript framework commonly used for UI?", "Django", "Spring", "React", "Flask", "C", "Programming", "EASY"),
    ("Which cricket player holds the record for most international centuries in men’s ODI cricket?", "Rohit Sharma", "Virat Kohli", "Sachin Tendulkar", "Chris Gayle", "B", "Cricket", "EASY"),
    ("Which year did India win the ICC Cricket World Cup under MS Dhoni?", "2011", "2015", "2007", "2013", "A", "Cricket", "EASY"),
    ("Which support metric measures the percentage of tickets resolved within the promised SLA?", "MTTR", "SLA compliance", "CSAT", "Uptime", "B", "Support", "EASY"),
    ("Which SQL command is used to fetch data from a table?", "INSERT", "DELETE", "SELECT", "UPDATE", "C", "Programming", "EASY"),
    ("Which of these is a Linux package manager on Ubuntu?", "npm", "apt", "pip", "mvn", "B", "Programming", "EASY"),

    ("Which Java collection allows duplicate values and maintains insertion order?", "HashSet", "TreeSet", "LinkedHashSet", "HashMap", "C", "Java", "MEDIUM"),
    ("What is the default value of a local Java variable?", "0", "null", "false", "Not assigned", "D", "Java", "MEDIUM"),
    ("Which Java keyword is used to prevent method overriding?", "final", "static", "private", "abstract", "A", "Java", "MEDIUM"),
    ("What is the time complexity of binary search on a sorted array?", "O(n)", "O(log n)", "O(n log n)", "O(1)", "B", "Programming", "MEDIUM"),
    ("Which HTTP status code means Not Found?", "200", "401", "404", "500", "C", "Programming", "MEDIUM"),
    ("Which team won the 2023 ICC Cricket World Cup?", "Australia", "England", "India", "New Zealand", "A", "Cricket", "MEDIUM"),
    ("Who was the first Indian to win a Nobel Prize?", "C. V. Raman", "Rabindranath Tagore", "Mother Teresa", "Homi Bhabha", "B", "Current Affairs", "MEDIUM"),
    ("Which of the following is used to reduce ticket backlog in IT support?", "Escalation matrix", "Firewall rule", "Database index", "Compiler flag", "A", "Support", "MEDIUM"),
    ("Which of these is a NoSQL database?", "MySQL", "Oracle", "MongoDB", "PostgreSQL", "C", "Programming", "MEDIUM"),
    ("Which current affairs event is associated with COP28?", "Climate summit in Dubai", "UN summit in Geneva", "G20 in Bali", "Olympic Games in Paris", "A", "Current Affairs", "MEDIUM"),

    ("Which Java interface is implemented by all Java collections?", "Comparable", "Iterable", "Serializable", "Cloneable", "B", "Java", "HARD"),
    ("What is the result of 3 + 2 * 5 in Java?", "25", "13", "17", "15", "B", "Java", "HARD"),
    ("Which Java method is called when an object is created?", "init()", "create()", "constructor", "start()", "C", "Java", "HARD"),
    ("Which principle in OOP means hiding internal implementation?", "Polymorphism", "Inheritance", "Encapsulation", "Abstraction", "C", "Programming", "HARD"),
    ("Which SQL clause is used to filter rows after grouping?", "WHERE", "HAVING", "GROUP BY", "LIMIT", "B", "Programming", "HARD"),
    ("Which cricket player scored the fastest ODI century in 31 balls?", "AB de Villiers", "Corey Anderson", "Rohit Sharma", "Jos Buttler", "A", "Cricket", "HARD"),
    ("Which cricketing record belongs to Muttiah Muralitharan?", "Most ODI wickets", "Most Test wickets", "Most T20 wickets", "Most sixes in T20Is", "B", "Cricket", "HARD"),
    ("Which support process tracks issues from creation to closure?", "Incident lifecycle", "Change freeze", "Patch window", "Release note", "A", "Support", "HARD"),
    ("What is the purpose of a Jira board?", "Hosting databases", "Tracking project work", "Running CI pipelines", "Publishing blogs", "B", "Support Project", "HARD"),
    ("Which country hosted the 2024 Summer Olympics?", "Japan", "France", "Italy", "USA", "B", "Current Affairs", "HARD"),

    ("Which Java keyword is used to inherit from another class?", "implements", "extends", "new", "this", "B", "Java", "EXPERT"),
    ("What is the output of Java compile step?", "Bytecode", "Machine code", "DLL", "HTML", "A", "Java", "EXPERT"),
    ("Which Java collection is best for constant-time lookup by key?", "ArrayList", "LinkedList", "HashMap", "Vector", "C", "Java", "EXPERT"),
    ("Which of these is a pure function property?", "Mutates global state", "Depends on external state", "Returns same output for same input", "Throws exceptions randomly", "C", "Programming", "EXPERT"),
    ("Which design pattern helps create objects without exposing construction logic?", "Singleton", "Factory", "Decorator", "Adapter", "B", "Programming", "EXPERT"),
    ("Which cricket record was set by Rohit Sharma in a single ODI innings?", "Highest individual score", "Fastest triple century", "Most wickets in an over", "Most catches", "A", "Cricket", "EXPERT"),
    ("Who is the current chairperson of the ICC?", "Jay Shah", "Ravi Shastri", "Anil Kumble", "Gary Kirsten", "A", "Cricket", "EXPERT"),
    ("Which support process is used for a sudden outage affecting all users?", "Change request", "Incident management", "Release management", "Budget tracking", "B", "Support", "EXPERT"),
    ("Which project artifact usually defines scope, timeline, and resources?", "Requirements doc", "Project charter", "Bug log", "Network diagram", "B", "Support Project", "EXPERT"),
    ("Which nation launched Chandrayaan-3?", "USA", "Russia", "India", "China", "C", "Current Affairs", "EXPERT"),

    ("Which Java class is the root of all exceptions?", "Throwable", "Exception", "Error", "RuntimeException", "A", "Java", "VERY_HARD"),
    ("Which Java feature supports multiple inheritance of behavior?", "Abstract class", "Interface", "Static class", "Final class", "B", "Java", "VERY_HARD"),
    ("What is the complexity of merge sort in the worst case?", "O(n)", "O(log n)", "O(n log n)", "O(n^2)", "C", "Programming", "VERY_HARD"),
    ("Which Java annotation is used to mark a method to override?", "@Deprecated", "@Override", "@SuppressWarnings", "@Test", "B", "Java", "VERY_HARD"),
    ("Which of the following is not a valid HTTP method?", "GET", "POST", "FETCH", "DELETE", "C", "Programming", "VERY_HARD"),
    ("Which cricket player scored 264 in a Test innings against Australia?", "Virat Kohli", "Rohit Sharma", "Gautam Gambhir", "Sachin Tendulkar", "B", "Cricket", "VERY_HARD"),
    ("Which famous cricket stadium is known as the 'Mecca of Cricket'?", "Wankhede Stadium", "Lord's", "Eden Gardens", "Melbourne Cricket Ground", "B", "Cricket", "VERY_HARD"),
    ("What does MTTR stand for in IT support?", "Mean Time To Recovery", "Maximum Ticket Tracking Rate", "Most Transactional Trouble Report", "Minimum Temporary Test Record", "A", "Support", "VERY_HARD"),
    ("Which Jira issue type is typically used for a defect?", "Story", "Bug", "Epic", "Task", "B", "Support Project", "VERY_HARD"),
    ("Which Indian startup is famously known for the UPI-based digital payment revolution?", "Paytm", "PhonePe", "Google Pay", "Amazon Pay", "B", "Current Affairs", "VERY_HARD"),
    ("Which Java collection implements FIFO ordering?", "HashMap", "PriorityQueue", "LinkedHashSet", "TreeMap", "B", "Java", "HARD"),
    ("Which support metric indicates average time to restore service after failure?", "CSAT", "MTTR", "SLA", "RTO", "B", "Support", "HARD"),
    ("Which cricketing feat is associated with Kapil Dev?", "Most ODI runs", "First Indian with 400 Test wickets", "Most T20 wickets", "Most sixes in IPL", "B", "Cricket", "HARD"),
    ("Which city hosted the 2023 G20 summit?", "New Delhi", "London", "Rio", "Tokyo", "A", "Current Affairs", "HARD"),
    ("Which project methodology is based on fixed scope and sequential phases?", "Agile", "Waterfall", "Scrum", "Kanban", "B", "Support Project", "HARD"),
    ("Which Java keyword is used to make a method belong to a class rather than an instance?", "this", "static", "final", "super", "B", "Java", "MEDIUM"),
    ("Which cricket player is famously known for hitting six sixes in an over?", "Yuvraj Singh", "MS Dhoni", "Rohit Sharma", "Suresh Raina", "A", "Cricket", "MEDIUM"),
    ("Which command in Git shows the current branch and status?", "git status", "git show", "git branch", "git diff", "A", "Programming", "EASY"),
    ("Which support concept means restoring service before full root cause is known?", "Root cause analysis", "Workaround", "Change freeze", "Backlog grooming", "B", "Support", "EXPERT"),
    ("Which of these is a JavaScript runtime environment?", "JDK", "JRE", "Node.js", "Spring Boot", "C", "Programming", "MEDIUM")
]

class Command(BaseCommand):
    help = "Seed a starter question bank."

    def handle(self, *args, **options):
        created = 0
        for row in QUESTIONS:
            text, a, b, c, d, correct, category, difficulty = row
            obj, was_created = Question.objects.get_or_create(
                question_text=text,
                defaults=dict(
                    option_a=a, option_b=b, option_c=c, option_d=d,
                    correct_option=correct, category=category,
                    difficulty=difficulty, active=True
                ),
            )
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(f"Seed complete. Added {created} new questions."))
