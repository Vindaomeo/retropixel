"""
Set regions in the matrix on 3 defined different colors.
Regions and colors are selected in the terminal.
Regions are set like coordinates (x1,x2,y1,y2), starting on x=1 and y=1 (not 0).

"""
import sys

from mx8 import Framebuffer, MatrixLink

# Hue normalized min and max for each color
COLOR_GRAY  = (180, 180, 180)  # subtiler ROI-Rahmen
COLOR_RED   = (0, 0, 255)      # aktiv, noch nicht erfüllt
COLOR_GREEN = (0, 255, 0)      # erfüllt

# ------------------------------------------------
# Led Matrix
# ------------------------------------------------
class LedMatrix:
    def __init__(self):
        # Init
        self.link = MatrixLink(ack_timeout=1.0,
                            on_log=lambda s: print(f"  LOG  {s}"))#port, baud, ack_timeout=1.0,
                            #on_log=lambda s: print(f"  LOG  {s}"))
        self.link.open()
        geo = self.link.wait_ready(timeout=8.0)
        width, height = geo.width, geo.height
        print(f"Port      {self.link.port} @ {self.link.baud} Baud")
        print(f"READY     {geo}")
        self.link.set_brightness(max(0, min(255, 48)))
        self.link.clear()
        self.link.send_hold()

        self.fb = Framebuffer(width, height)

    def render(self, color:tuple = COLOR_GRAY, reg: dict = {'x': [1,1], 'y': [1,1]}) -> None:
        """Draw one frame into the framebuffer. Set pixel region in one color.
            Send the buffer via serial port and set the controller on Hold.""" 
        
        b,g,r = color
        
        for y in range(reg['y'][0], reg['y'][1]+1):
            for x in range(reg['x'][0], reg['x'][1]+1):
                self.fb.set(x, y, r, g, b)
  
        self.link.send_frame(self.fb.to_bytes())
        self.link.send_hold()

def region():
    reg = {"x": [],
           "y": []}
    
    x = int(input("x0: "))
    reg["x"].append(x-1)
    x = int(input("x1: "))
    reg["x"].append(x-1)
    x = int(input("y0: "))
    reg["y"].append(x-1)
    x = int(input("y1: "))
    reg["y"].append(x-1)

    return reg

# ------------------------------------------------
# -------------------- Main ----------------------
# ------------------------------------------------

def main() -> int:
    #global link, fb

    lm = LedMatrix()
    reg = None

    # Main loop
    try:
        while True:
            print("Region 1:")
            reg = region() 
            cnew = int(input("Color (1=red, 2=green o 3=gray): "))
            if cnew == 1: c = COLOR_RED
            elif cnew == 2: c = COLOR_GREEN
            else: c = COLOR_GRAY    
            lm.fb.clear()     
            lm.render(c, reg)

            print("Region 2:")
            reg = region()
            cnew = int(input("Color (1=red, 2=green o 3=gray): "))
            if cnew == 1: c = COLOR_RED
            elif cnew == 2: c = COLOR_GREEN
            else: c = COLOR_GRAY         
            lm.render(c, reg)
    except KeyboardInterrupt:
        print("Interrupted")
        if lm.link:
           lm.link.clear()
           lm.link.close()
        return 130
    except (TimeoutError, OSError) as e:
        print(f"\nConnection lost: {e}")
        return 1
    finally:
        if lm.link:
            lm.link.close()


if __name__ == "__main__":
    sys.exit(main())